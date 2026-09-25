#!/usr/bin/env python3
"""Fleet view for orch-dash: every Orca session under one top-level orchestrator, the PRs they own with their
reviewers, and what the human has to act on. Deterministic and model-free, like the program view.

Sources are local CLIs only (`orca`, `gh`, `git`). Nothing is sent anywhere but GitHub's own API through `gh`,
and the state lives outside the repository:

  $ORCH_FLEET_STATE  (default ~/.local/state/agent-skills/dashboard)   state.json, prs.json, inbox.json, events,
                                                                       prompts/<terminal>.json (hooks/permission.py),
                                                                       decisions.json (`orch decide`)
  $ORCH_FLEET_CONFIG (default ~/.config/agent-skills/dashboard/config.json)

Everything written is masked first (tokens, auth headers, *_TOKEN=...): prompts and tool inputs are raw text.
"""
import collections, concurrent.futures, datetime as dt, fcntl, hashlib, json, os, pathlib, re, subprocess, sys, tempfile, threading, time

ORCA_EVERY = 10          # worktree ps, terminal list, inbox: three local calls, ~0.5 s together
RUNS_EVERY = 120         # runs, workers, tasks, gates
PR_TICK = 90             # PR probe tick; each PR is due per its tier below
DISCOVER_EVERY = 600     # re-ask GitHub which PR a branch has, for sessions that had none
TIER_EVERY = {"hot": 90, "warm": 300, "cold": 900, "done": 86400}
EVENTS_KEEP = 400
ROTATE_BYTES = 5 * 1024 * 1024  # events.jsonl and pr-events.jsonl move to *.1 past this
STICKY_TYPES = ("approval", "run_command", "login", "verify_failed", "question")
BRANCH_SKIP = {"main", "master", "develop", "trunk"}
LOGIN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?")


class SourceError(Exception):
    pass


# ---------------------------------------------------------------- paths, time, io

def state_dir():
    return pathlib.Path(os.environ.get("ORCH_FLEET_STATE", "~/.local/state/agent-skills/dashboard")).expanduser()


def config_path():
    return pathlib.Path(os.environ.get("ORCH_FLEET_CONFIG", "~/.config/agent-skills/dashboard/config.json")).expanduser()


def config():
    return read_json(config_path(), {}) or {}


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ") if t else None


def ms_iso(v):
    return iso(dt.datetime.fromtimestamp(v / 1000, dt.timezone.utc)) if isinstance(v, (int, float)) and v else None


def parse(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def read_json(path, default=None):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return default


def write_atomic(path, text):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb" if isinstance(text, bytes) else "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


def append_line(path, row):
    """One write() under O_APPEND and under 4 KiB, so a reader never sees half a line."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    if len(line) >= 4096:
        raise ValueError("event line over 4 KiB")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def rotate(path):
    try:
        if path.stat().st_size > ROTATE_BYTES:
            os.replace(path, path.with_name(path.name + ".1"))
    except OSError:
        pass


def read_lines(path, keep=None):
    try:
        lines = pathlib.Path(path).read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-keep:] if keep else lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def digest(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- masking

SECRET_RE = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|sk-ant-[A-Za-z0-9_-]{10,}"
    r"|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}")
ASSIGN_RE = re.compile(r"\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|CUSTOM_HEADERS)[A-Z0-9_]*)"
                       r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)", re.I)
AUTH_RE = re.compile(r"\b(Authorization|Proxy-Authorization|X-Api-Key|Bearer|Basic)(\s*[:=]?\s+)([^\s\"']+)", re.I)


def mask(text, limit=None):
    """Hide credentials in free text. Applied to everything collected before it is written or shown."""
    if not text:
        return text
    s = SECRET_RE.sub("[masked]", str(text))
    s = ASSIGN_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[masked]", s)
    s = AUTH_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[masked]", s)
    if limit and len(s) > limit:
        s = s[:limit - 1] + "…"
    return s


# ---------------------------------------------------------------- CLI sources

def run_json(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise SourceError(f"{cmd[0]} not found")
    except subprocess.TimeoutExpired:
        raise SourceError(f"{cmd[0]} timed out after {timeout}s")
    try:
        out = json.loads(r.stdout) if r.stdout.strip() else None
    except ValueError:
        out = None
    if r.returncode and out is None:
        tail = (r.stderr or r.stdout).strip().splitlines()
        raise SourceError(mask(tail[-1][:300]) if tail else f"{cmd[0]} exit {r.returncode}")
    if out is None:
        raise SourceError(f"{cmd[0]}: output was not JSON")
    return out


def orca(*args, timeout=30):
    out = run_json(["orca", *args, "--json"], timeout)
    if isinstance(out, dict) and out.get("ok") is False:
        raise SourceError(mask(json.dumps(out.get("error"))[:300]))
    return out.get("result", out) if isinstance(out, dict) else {}


def fetch_fast():
    calls = {"worktrees": ("worktree", "ps", "--limit", "500"), "terminals": ("terminal", "list"),
             "messages": ("orchestration", "inbox", "--limit", "100")}
    with concurrent.futures.ThreadPoolExecutor(len(calls)) as pool:
        futures = {k: pool.submit(orca, *args) for k, args in calls.items()}
        return {k: f.result().get(k, []) for k, f in futures.items()}


def fetch_runs():
    runs = [r for r in orca("orchestration", "run-list").get("runs", []) if not r.get("legacy")
            and r.get("id") != "run_legacy_local"]
    workers, cursor = [], None
    while True:
        res = orca("orchestration", "worker-list", "--limit", "100", *(["--cursor", cursor] if cursor else []))
        workers += res.get("workers", [])
        cursor = (res.get("page") or {}).get("nextCursor")
        if not (res.get("page") or {}).get("hasMore") or not cursor:
            break
    tasks, gates = [], []
    for r in runs:
        tasks += orca("orchestration", "task-list", "--run", r["id"]).get("tasks", [])
        gates += orca("orchestration", "gate-list", "--run", r["id"]).get("gates", [])
    return {"runs": runs, "workers": workers, "tasks": tasks, "gates": gates}


def graphql(query):
    """(data, errors). gh exits non-zero when any alias errors, even with the rest of the data present, so one
    inaccessible PR must not blank the others."""
    out = run_json(["gh", "api", "graphql", "-f", f"query={query}"], timeout=60)
    data = out.get("data") if isinstance(out, dict) else None
    errors = [mask(e.get("message", "")[:200]) for e in (out.get("errors") or [])] if isinstance(out, dict) else []
    if data is None:
        raise SourceError("; ".join(errors) or "GraphQL returned no data")
    return data, errors


def gql_str(s):
    return json.dumps(str(s))


def repo_of(path, cache):
    """owner/name of the worktree's GitHub origin, or None."""
    if path in cache:
        return cache[path]
    slug = None
    try:
        url = subprocess.run(["git", "-C", path, "remote", "get-url", "origin"], capture_output=True, text=True,
                             timeout=5).stdout.strip()
        m = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?$", url)
        slug = f"{m.group(1)}/{m.group(2)}" if m else None
    except (OSError, subprocess.TimeoutExpired):
        pass
    cache[path] = slug
    return slug


# ---------------------------------------------------------------- sessions and hierarchy

def short_branch(ref):
    return (ref or "").removeprefix("refs/heads/")


def agent_row(a):
    return {"pane": a.get("paneKey"), "state": a.get("state"), "type": a.get("agentType"),
            "mode": a.get("workingMode"), "tool": a.get("toolName"), "detail": mask(a.get("toolInput"), 140),
            "since": ms_iso(a.get("stateStartedAt")), "updated": ms_iso(a.get("updatedAt")),
            "title": mask(a.get("taskTitle") or a.get("displayName"), 120),
            "prompt": mask(a.get("prompt"), 400), "last": mask(a.get("lastAssistantMessage"), 1200),
            "interrupted": bool(a.get("interrupted"))}


def phase_of(agents, live_terms):
    states = {a["state"] for a in agents}
    if "waiting" in states:
        return "waiting"
    if "working" in states:
        return "working"
    if "done" in states:
        return "idle"
    return "open" if live_terms else "offline"


def build_sessions(fast, runs, cfg):
    terms_by_wt = {}
    for t in fast["terminals"]:
        terms_by_wt.setdefault(t.get("worktreeId"), []).append(
            {"handle": t.get("handle"), "pane": f"{t.get('tabId')}:{t.get('leafId')}",  # an agent's paneKey
             "title": mask(t.get("title"), 120), "agent": t.get("agentIdentity"),
             "writable": bool(t.get("writable")), "connected": bool(t.get("connected")),
             "last_output": ms_iso(t.get("lastOutputAt"))})
    wt_of_handle = {t.get("handle"): t.get("worktreeId") for t in fast["terminals"]}
    sessions = {}
    for w in fast["worktrees"]:
        if w.get("isArchived"):
            continue
        agents = [agent_row(a) for a in w.get("agents") or []]
        terms = terms_by_wt.get(w["worktreeId"], [])
        if w.get("isMainWorktree") and not agents and not cfg.get("include_main_worktrees"):
            continue
        sessions[w["worktreeId"]] = {
            "id": w["worktreeId"], "name": mask(w.get("displayName") or pathlib.Path(w.get("path", "")).name, 120),
            "repo_name": w.get("repo"), "branch": short_branch(w.get("branch")), "path": w.get("path"),
            "status": w.get("status"), "orca_unread": bool(w.get("unread")), "comment": mask(w.get("comment"), 200),
            "last_activity": ms_iso(w.get("lastActivityAt")), "orca_parent": w.get("parentWorktreeId"),
            "linked_pr": w.get("linkedPR"), "agents": agents, "terminals": terms,
            "phase": phase_of(agents, terms), "kind": "task", "parent": None, "run": None, "dispatch": None,
            "dispatch_status": None, "task_title": None}

    # Orchestration: a Run's coordinator worktree, found through the coordinator's live terminal.
    coord_of_run = {r["id"]: wt_of_handle.get(r.get("coordinator_handle")) for r in runs["runs"]}
    tasks = {t["id"]: t for t in runs["tasks"]}
    by_wt = {}
    for w in runs["workers"]:
        wt = (w.get("resource") or {}).get("worktreeId")
        if not wt:
            continue
        prev = by_wt.get(wt)
        live = w.get("dispatchStatus") not in ("completed", "failed", "abandoned")
        if not prev or (live and not prev[1]):
            by_wt[wt] = (w, live)

    root = cfg.get("root_worktree") if cfg.get("root_worktree") in sessions else None
    if not root:  # newest Run whose coordinator is on screen
        for r in sorted(runs["runs"], key=lambda r: r.get("updated_at") or "", reverse=True):
            if coord_of_run.get(r["id"]) in sessions:
                root = coord_of_run[r["id"]]
                break
    root_runs = {rid for rid, wt in coord_of_run.items() if wt and wt == root}

    for sid, s in sessions.items():
        w = by_wt.get(sid, (None, False))[0]
        if w:
            t = tasks.get(w.get("taskId")) or {}
            s.update(run=w.get("runId"), dispatch=w.get("dispatchId"), dispatch_status=w.get("dispatchStatus"),
                     task_title=mask(t.get("display_name") or t.get("task_title"), 160),
                     dispatch_live=by_wt[sid][1], terminal=w.get("agentTerminalHandle"))
        if sid == root:
            s["kind"] = "orchestrator"
        elif sid in coord_of_run.values():
            s["kind"] = "orchestration"
            s["parent"] = root
            s["run"] = next(rid for rid, wt in coord_of_run.items() if wt == sid)
        elif w and w.get("runId") not in root_runs and coord_of_run.get(w.get("runId")) in sessions:
            s["parent"] = coord_of_run[w["runId"]]
        else:
            s["parent"] = root
            s["kind"] = "task" if w else "standalone"
    return sessions, root


# ---------------------------------------------------------------- inbox classification

RULES = [  # (type, regex) on the end of a finished turn; the first match wins
    ("verify_failed", re.compile(r"(동작\s*확인|검증|verif|E2E|smoke|테스트)[^\n]{0,80}(실패|failed|fail\b|❌|깨졌|안\s*됨)"
                                 r"|(실패|failed|❌)[^\n]{0,60}(동작\s*확인|검증|verif|E2E)", re.I)),
    ("login", re.compile(r"(로그인|login|log in|sign in|재인증|인증해|authori[sz]e|SSO|OAuth)[^\n]{0,80}"
                         r"(해\s*주세요|필요|부탁|please|required|열어)", re.I)),
    ("run_command", re.compile(r"(^|\s)`?!\s?[a-z][\w.-]+ |직접\s*(실행|입력|쳐)|실행해\s*주세요|run (this|the following)"
                               r"|터미널에서[^\n]{0,40}(실행|입력)", re.I | re.M)),
    ("approval", re.compile(r"(승인|괜찮을까요|진행할까요|진행해도\s*될까요|할까요\?|OK\?|확인\s*부탁|결정해\s*주세요|골라\s*주세요"
                            r"|進めてよい|よろしいですか|approve|shall I|should I)", re.I)),
    ("verify_ok", re.compile(r"(동작\s*확인|검증|verif|E2E)[^\n]{0,60}(완료|통과|passed|green|✅|성공)", re.I)),
]


def classify(text):
    tail = (text or "")[-1500:]
    for kind, rx in RULES:
        if rx.search(tail):
            return kind
    return "fyi"


def first_line(text, limit=160):
    for line in (text or "").splitlines():
        line = line.strip(" #*>-\t")
        if line:
            return mask(line, limit)
    return ""


# ---------------------------------------------------------------- permission prompts (recorded by hooks/permission.py)

QUESTION_RE = re.compile(r"Do you want to .+\?")
OPTION_RE = re.compile(r"(?:❯\s*)?(\d)\.\s+(.+)")
RULE_RE = re.compile(r"─{10,}")
PROMPT_KEEP_S = 86400


def parse_dialog(lines):
    """The permission dialog at the bottom of a rendered Claude Code screen, or None. Only the shape measured on
    2.1.282 counts: a rule, the request, "Do you want to …?", numbered options, and "Esc to cancel …" as the last
    line. A question list (AskUserQuestion) or the folder trust dialog has no such question and does not match."""
    body = [l.replace("\xa0", " ").strip() for l in lines]
    while body and not body[-1]:
        body.pop()
    if not body or not body[-1].startswith("Esc to cancel"):
        return None
    q = next((i for i in range(len(body) - 2, -1, -1) if QUESTION_RE.fullmatch(body[i])), None)
    if q is None:
        return None
    options = []
    for line in filter(None, body[q + 1:-1]):
        m = OPTION_RE.fullmatch(line)
        if m:
            options.append([m.group(1), m.group(2)])
        elif options:  # a long label wraps onto the next line
            options[-1][1] += " " + line
        else:
            return None
    if not options:
        return None
    top = max((i for i in range(q) if RULE_RE.fullmatch(body[i])), default=-1)
    return {"question": body[q], "request": [l for l in body[top + 1:q] if l], "lines": body[top + 1:],
            # "Yes, and switch to auto mode · auto mode handles these prompts for you": the label is before " · "
            "options": [(d, label.split(" · ")[0].strip()) for d, label in options]}


def prompt_needles(rec):
    """What the dialog must show of the recorded request: the command, the file name, the URL host, or else any
    string argument (an MCP tool's dialog lists them)."""
    inp = rec.get("input") or {}
    if inp.get("command"):
        return [inp["command"]]
    if inp.get("file_path") or inp.get("notebook_path"):
        return [pathlib.PurePath(inp.get("file_path") or inp["notebook_path"]).name]
    if inp.get("url"):
        return [re.sub(r"^\w+://([^/]+).*", r"\1", inp["url"])]
    return [v for v in inp.values() if v]


def prompt_verdict(lines, rec):
    """(dialog, None) when the screen shows the permission dialog for the recorded request, else (None, reason)."""
    d = parse_dialog(lines)
    if not d:
        return None, "no permission dialog on screen"
    # Whitespace is dropped on both sides: the dialog wraps a long command wherever the terminal width falls.
    shown = "".join("".join(d["request"] + [d["question"]]).split())
    for n in prompt_needles(rec):
        n = "".join(n.split())[:40]
        if len(n) >= 3 and n in shown:
            return d, None
    return None, "the dialog on screen is not the recorded request"


def option_for(dialog, action):
    """The digit that answers this one request: the bare "Yes" to approve, "No" to deny. An option that saves a
    rule or changes the mode ("don't ask again", "always allow", "switch to auto mode") is never chosen."""
    want = (lambda l: l == "Yes") if action == "approve" else (lambda l: l == "No" or l.startswith("No,"))
    return next((d for d, label in dialog["options"] if want(label)), None)


def prompt_records():
    """{terminal handle: record} from prompts/, dropping records older than a day."""
    out, cutoff = {}, iso(utcnow() - dt.timedelta(seconds=PROMPT_KEEP_S))
    for path in (state_dir() / "prompts").glob("*.json"):
        rec = read_json(path)
        if not isinstance(rec, dict) or (rec.get("at") or "") < cutoff:
            path.unlink(missing_ok=True)
        elif rec.get("handle") == path.stem:
            out[path.stem] = rec
    return out


def read_screen(handle):
    t = orca("terminal", "read", "--terminal", handle, "--screen", timeout=10).get("terminal") or {}
    return (t.get("tail") or []) if t.get("source") in (None, "screen") else []


# ---------------------------------------------------------------- PRs

PROBE = "number state isDraft updatedAt headRefOid url title commits(last:1){nodes{commit{statusCheckRollup{state}}}}"
DETAIL = PROBE + (
    " author{login} reviewDecision"
    " reviewRequests(first:20){nodes{requestedReviewer{__typename ... on User{login avatarUrl} ... on Team{slug}"
    " ... on Bot{login avatarUrl}}}}"
    " latestOpinionatedReviews(first:20){nodes{state submittedAt author{__typename login avatarUrl} commit{oid}}}"
    " reviews(last:30){nodes{id state submittedAt author{__typename login avatarUrl}}}"
    " reviewThreads(first:100){totalCount nodes{id isResolved comments(last:1){nodes{id createdAt url"
    " author{__typename login}}}}}")


def pr_key(repo, number):
    return f"{repo}#{number}"


def pr_query(keys, body):
    parts = []
    for i, k in enumerate(keys):
        repo, n = k.split("#")
        owner, name = repo.split("/")
        parts.append(f"p{i}: repository(owner:{gql_str(owner)},name:{gql_str(name)})"
                     f"{{pullRequest(number:{int(n)}){{{body}}}}}")
    return "query{" + " ".join(parts) + " rateLimit{cost remaining}}"


def discover_query(pairs):
    parts = []
    for i, (repo, branch) in enumerate(pairs):
        owner, name = repo.split("/")
        parts.append(f"b{i}: repository(owner:{gql_str(owner)},name:{gql_str(name)}){{ref(qualifiedName:"
                     f"{gql_str('refs/heads/' + branch)}){{associatedPullRequests(first:3,orderBy:{{field:UPDATED_AT,"
                     f"direction:DESC}}){{nodes{{number state}}}}}}}}")
    return "query{" + " ".join(parts) + " rateLimit{cost remaining}}"


def rollup(pr):
    nodes = (pr.get("commits") or {}).get("nodes") or []
    st = (((nodes[0] if nodes else {}).get("commit") or {}).get("statusCheckRollup") or {}).get("state")
    return {"SUCCESS": "success", "FAILURE": "failure", "ERROR": "failure", "PENDING": "pending",
            "EXPECTED": "pending"}.get(st)


def probe_sig(pr):
    return [pr.get("state"), pr.get("updatedAt"), pr.get("headRefOid"), rollup(pr)]


def is_bot(author):
    a = author or {}
    return a.get("__typename") == "Bot" or str(a.get("login", "")).endswith("[bot]")


def shape_pr(repo, pr, me):
    """What the page and the event diff need from one PR's detail."""
    head = pr.get("headRefOid")
    reviewers = {}
    for n in (pr.get("reviewRequests") or {}).get("nodes") or []:
        r = n.get("requestedReviewer") or {}
        login = r.get("login") or (f"team:{r['slug']}" if r.get("slug") else None)
        if login:
            reviewers[login] = {"login": login, "avatar": r.get("avatarUrl"), "requested": True, "state": None,
                                "at": None, "stale": False, "team": bool(r.get("slug"))}
    for n in (pr.get("latestOpinionatedReviews") or {}).get("nodes") or []:
        a = n.get("author") or {}
        if not a.get("login") or a.get("login") == me:
            continue
        r = reviewers.setdefault(a["login"], {"login": a["login"], "avatar": a.get("avatarUrl"), "requested": False,
                                              "team": False})
        r.update(state=n.get("state"), at=n.get("submittedAt"), avatar=r.get("avatar") or a.get("avatarUrl"),
                 stale=n.get("state") == "APPROVED" and (n.get("commit") or {}).get("oid") != head,
                 bot=is_bot(a))
    for n in (pr.get("reviews") or {}).get("nodes") or []:  # commented-only reviewers never show in "latest"
        a = n.get("author") or {}
        if a.get("login") and a["login"] != me and a["login"] not in reviewers and n.get("state") == "COMMENTED":
            reviewers[a["login"]] = {"login": a["login"], "avatar": a.get("avatarUrl"), "requested": False,
                                     "state": "COMMENTED", "at": n.get("submittedAt"), "stale": False, "team": False,
                                     "bot": is_bot(a)}
    threads = []
    for t in (pr.get("reviewThreads") or {}).get("nodes") or []:
        c = ((t.get("comments") or {}).get("nodes") or [{}])[-1]
        threads.append({"id": t.get("id"), "resolved": bool(t.get("isResolved")), "last_id": c.get("id"),
                        "last_at": c.get("createdAt"), "last_url": c.get("url"),
                        "last_by": (c.get("author") or {}).get("login"), "last_bot": is_bot(c.get("author"))})
    for r in reviewers.values():
        r["status"] = edge_status(r)
    return {"key": pr_key(repo, pr["number"]), "repo": repo, "number": pr["number"], "url": pr.get("url"),
            "title": mask(pr.get("title"), 200), "state": pr.get("state"), "draft": bool(pr.get("isDraft")),
            "updated": pr.get("updatedAt"), "head": head, "ci": rollup(pr), "decision": pr.get("reviewDecision"),
            "author": (pr.get("author") or {}).get("login"), "reviewers": sorted(reviewers.values(), key=lambda r: r["login"]),
            "threads": threads, "reviews": [{"id": n.get("id"), "state": n.get("state"), "at": n.get("submittedAt"),
                                             "by": (n.get("author") or {}).get("login"),
                                             "bot": is_bot(n.get("author"))}
                                            for n in (pr.get("reviews") or {}).get("nodes") or []]}


def edge_status(r):
    """The reviewer edge's colour class. Slack evidence is not read yet, so "requested" is one state."""
    if r.get("state") == "APPROVED":
        return "approved_stale" if r.get("stale") else "approved"
    if r.get("state") == "CHANGES_REQUESTED":
        return "changes_requested"
    if r.get("state") == "COMMENTED":
        return "commented"
    return "requested" if r.get("requested") else "none"


def open_threads(p, me):
    return [t for t in p["threads"] if not t["resolved"] and t["last_by"] and t["last_by"] != me]


def pr_items(p, me, cfg):
    """Live inbox items for one PR: open while the condition holds."""
    if p["state"] != "OPEN":
        return []
    out, bots = [], cfg.get("bots_in_inbox", False)
    base = {"pr": p["key"], "url": p["url"]}
    if any(r.get("state") == "CHANGES_REQUESTED" for r in p["reviewers"]):
        who = ", ".join(r["login"] for r in p["reviewers"] if r.get("state") == "CHANGES_REQUESTED")
        out.append({**base, "type": "changes_requested", "title": f"변경 요청: {who}"})
    threads = [t for t in open_threads(p, me) if bots or not t["last_bot"]]
    if threads:
        out.append({**base, "type": "review_comment_received", "title": f"미해결 리뷰 스레드 {len(threads)}개",
                    "url": threads[-1]["last_url"] or p["url"]})
    if p["ci"] == "failure":
        out.append({**base, "type": "ci_failed", "title": "CI 실패"})
    stale = [r["login"] for r in p["reviewers"] if r.get("stale")]
    fresh = [r for r in p["reviewers"] if r.get("state") == "APPROVED" and not r.get("stale")]
    if stale and not fresh:
        out.append({**base, "type": "approval_stale", "title": f"승인이 이전 커밋 기준: {', '.join(stale)}"})
    if fresh and p["ci"] in ("success", None) and not p["draft"] and p["decision"] in ("APPROVED", None):
        out.append({**base, "type": "ready_to_merge", "title": "승인됨, CI 통과: 머지 판단 대기"})
    return out


def pr_events(prev, cur, me):
    """Events between two detail snapshots of one PR (prev may be None: then only the state is recorded)."""
    if prev is None:
        return []
    ev = []
    add = lambda kind, sid, actor=None, url=None: ev.append({"kind": kind, "source": sid, "actor": actor,
                                                              "url": url or cur["url"]})
    seen = {r["id"] for r in prev.get("reviews", [])}
    for r in cur["reviews"]:
        if r["id"] in seen or r["by"] == me:
            continue
        kind = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes_requested", "COMMENTED": "review_comment"}.get(r["state"])
        if kind:
            add(kind, r["id"], r["by"])
    known = {t["id"]: t for t in prev.get("threads", [])}
    for t in cur["threads"]:
        old = known.get(t["id"], {})
        if t["last_id"] and t["last_id"] != old.get("last_id") and t["last_by"] != me:
            add("review_comment", t["last_id"], t["last_by"], t["last_url"])
    req_prev = {r["login"] for r in prev.get("reviewers", []) if r.get("requested")}
    req_cur = {r["login"] for r in cur["reviewers"] if r.get("requested")}
    for login in sorted(req_cur - req_prev):
        add("review_requested", f"req:{login}:{cur['updated']}", login)
    if prev.get("head") and cur["head"] != prev["head"]:
        add("head_pushed", cur["head"])
        if any(r.get("state") == "APPROVED" for r in prev.get("reviewers", [])):
            add("approval_stale", f"stale:{cur['head']}")
    if cur["ci"] == "failure" and prev.get("ci") != "failure":
        add("checks_failed", f"ci:{cur['head']}:fail")
    if prev.get("ci") == "failure" and cur["ci"] == "success":
        add("checks_recovered", f"ci:{cur['head']}:ok")
    if cur["state"] != prev.get("state") and cur["state"] in ("MERGED", "CLOSED"):
        add(cur["state"].lower(), f"state:{cur['state']}")
    return ev


def tier(p, session_phase, now):
    if p["state"] != "OPEN":
        return "done"
    upd = parse(p.get("updated"))
    if p.get("ci") == "pending" or session_phase == "working" or (upd and now - upd < dt.timedelta(hours=2)):
        return "hot"
    return "warm" if upd and now - upd < dt.timedelta(hours=24) else "cold"


# ---------------------------------------------------------------- the collector

class Fleet:
    """Holds the caches between ticks and writes state.json. One instance per server process."""

    def __init__(self, fetch_fast=fetch_fast, fetch_runs=fetch_runs, graphql=graphql, now=utcnow, read_screen=read_screen):
        self.fetch_fast, self.fetch_runs, self.graphql, self.now = fetch_fast, fetch_runs, graphql, now
        self.read_screen = read_screen
        self.lock = threading.Lock()
        d = state_dir()
        self.fast, self.runs = None, {"runs": [], "workers": [], "tasks": [], "gates": []}
        self.sources = {k: {"ok": None, "updated_at": None, "error": None} for k in ("orca", "runs", "github")}
        self.repos = {}
        saved = read_json(d / "prs.json", {}) or {}
        self.prs = saved.get("prs", {})           # key -> {"probe": sig, "detail": shaped, "probed_at": iso}
        self.branch_pr = saved.get("branch_pr", {})  # "repo@branch" -> {"key": key|None, "at": iso}
        self.me = saved.get("me")
        self.mem = read_json(d / "memory.json", {}) or {}  # prompt hashes, last states, sticky inbox
        self.last = {"runs": 0.0, "prs": 0.0}
        self.state, self.owner_of, self.saved = None, {}, {}
        self.events = collections.deque(read_lines(d / "events.jsonl", EVENTS_KEEP), maxlen=EVENTS_KEEP)

    # -- persistence

    def save(self, **files):
        """Write each {name: text} whose text differs from the last write (a tick usually changes nothing)."""
        for name, text in files.items():
            if self.saved.get(name) != text:
                write_atomic(state_dir() / name, text)
                self.saved[name] = text

    def event(self, row):
        path = state_dir() / "events.jsonl"
        rotate(path)
        append_line(path, row)
        self.events.append(row)

    def ok(self, name, err=None):
        s = self.sources[name]
        s["ok"], s["error"] = err is None, err
        if err is None:
            s["updated_at"] = iso(self.now())

    # -- ticks

    def tick(self, force=False):
        """Refresh whatever is due and rewrite state.json. Returns the new state."""
        with self.lock:
            mono = time.monotonic()
            try:
                self.fast = self.fetch_fast()
                self.ok("orca")
            except SourceError as e:
                self.ok("orca", str(e))
            if force or not self.last["runs"] or mono - self.last["runs"] >= RUNS_EVERY:
                try:
                    self.runs = self.fetch_runs()
                    self.ok("runs")
                except SourceError as e:
                    self.ok("runs", str(e))
                self.last["runs"] = mono
            if self.fast is None:
                return self.state
            cfg = config()
            sessions, root = build_sessions(self.fast, self.runs, cfg)
            for s in sessions.values():
                s["gh_repo"] = repo_of(s["path"], self.repos) if s.get("path") else None
            if force or mono - self.last["prs"] >= PR_TICK:
                try:
                    self.refresh_prs(sessions, cfg)
                    self.ok("github")
                except SourceError as e:
                    self.ok("github", str(e))
                self.last["prs"] = mono
            self.state = self.compose(sessions, root, cfg)
            self.save(**{"state.json": json.dumps(self.state, ensure_ascii=False),
                         "prs.json": json.dumps({"prs": self.prs, "branch_pr": self.branch_pr, "me": self.me}),
                         "memory.json": json.dumps(self.mem, ensure_ascii=False)})
            return self.state

    def refresh_prs(self, sessions, cfg):
        now = self.now()
        if not self.me:
            try:
                self.me = run_json(["gh", "api", "user"]).get("login")
            except SourceError:
                self.me = None
        # 1. Which PR does each session's branch have (cached; a branch without one is re-asked every 10 min).
        ask, owner_of = [], {}
        for s in sessions.values():
            repo = s["gh_repo"]
            if not repo:
                continue
            if isinstance(s.get("linked_pr"), dict) and s["linked_pr"].get("number"):
                k = pr_key(repo, s["linked_pr"]["number"])
                owner_of.setdefault(k, s["id"])
            if not s["branch"] or s["branch"] in BRANCH_SKIP:
                continue
            bk = f"{repo}@{s['branch']}"
            hit = self.branch_pr.get(bk)
            if not hit or (hit.get("key") is None and now - (parse(hit.get("at")) or now) >= dt.timedelta(seconds=DISCOVER_EVERY)):
                ask.append((repo, s["branch"]))
            elif hit.get("key"):
                owner_of.setdefault(hit["key"], s["id"])
        errors = []
        for i in range(0, len(ask), 40):
            chunk = ask[i:i + 40]
            data, errs = self.graphql(discover_query(chunk))
            errors += errs
            for j, (repo, branch) in enumerate(chunk):
                nodes = ((((data.get(f"b{j}") or {}).get("ref") or {}).get("associatedPullRequests") or {})
                         .get("nodes") or [])
                open_first = sorted(nodes, key=lambda n: n.get("state") != "OPEN")
                key = pr_key(repo, open_first[0]["number"]) if open_first else None
                self.branch_pr[f"{repo}@{branch}"] = {"key": key, "at": iso(now)}
                if key:
                    owner_of.setdefault(key, next(s["id"] for s in sessions.values()
                                                  if s.get("gh_repo") == repo and s["branch"] == branch))
        self.owner_of = owner_of
        live = {f"{s['gh_repo']}@{s['branch']}" for s in sessions.values()}
        self.prs = {k: v for k, v in self.prs.items() if k in owner_of}
        self.branch_pr = {k: v for k, v in self.branch_pr.items() if k in live}
        # 2. Probe every PR whose tier says it is due; 3. detail for the ones that changed.
        due = []
        for k, sid in owner_of.items():
            entry = self.prs.get(k)
            if entry and entry.get("detail"):
                t = tier(entry["detail"], sessions.get(sid, {}).get("phase"), now)
                last = parse(entry.get("probed_at"))
                every = TIER_EVERY[t]
                if last and (now - last).total_seconds() < every - 5:
                    continue
            due.append(k)
        changed = []
        for i in range(0, len(due), 50):
            chunk = due[i:i + 50]
            data, errs = self.graphql(pr_query(chunk, PROBE))
            errors += errs
            for j, k in enumerate(chunk):
                pr = (data.get(f"p{j}") or {}).get("pullRequest")
                if not pr:
                    continue
                entry = self.prs.setdefault(k, {})
                sig = probe_sig(pr)
                if sig != entry.get("probe") or not entry.get("detail"):
                    changed.append(k)
                entry["probe"], entry["probed_at"] = sig, iso(now)
        for i in range(0, len(changed), 25):
            chunk = changed[i:i + 25]
            data, errs = self.graphql(pr_query(chunk, DETAIL))
            errors += errs
            for j, k in enumerate(chunk):
                pr = (data.get(f"p{j}") or {}).get("pullRequest")
                if not pr:
                    continue
                cur = shape_pr(k.split("#")[0], pr, self.me)
                prev = self.prs[k].get("detail")
                for e in pr_events(prev, cur, self.me):
                    self.emit_pr_event(cur, e, sessions.get(owner_of.get(k)), now)
                self.prs[k]["detail"] = cur
                self.fetch_avatars(cur)
        if errors:
            raise SourceError("; ".join(errors[:3]))

    def emit_pr_event(self, pr, e, session, now):
        owner, kind = None, None
        if session:
            term = next((t["handle"] for t in session["terminals"] if t.get("agent")), None)
            if session.get("dispatch") and session.get("dispatch_live"):
                owner, kind = session["dispatch"], "dispatch"
            elif session["kind"] == "standalone":
                owner, kind = term, "human_session"
            elif term:
                owner, kind = term, "terminal"
        row = {"v": 1, "id": digest(pr["key"], e["kind"], e["source"]), "at": iso(now), "repo": pr["repo"],
               "pr": pr["number"], "kind": e["kind"], "url": e["url"], "owner": owner, "owner_kind": kind,
               "actor": e.get("actor"), "checks": pr["ci"], "head": pr["head"]}
        path = state_dir() / "pr-events.jsonl"
        rotate(path)
        append_line(path, row)
        self.event({"at": row["at"], "kind": f"pr_{e['kind']}", "session": session and session["id"],
                    "pr": pr["key"], "actor": e.get("actor"), "url": e["url"],
                    "text": f"#{pr['number']} {pr['title'] or ''}" + (f" · {e['actor']}" if e.get("actor") else "")})

    def fetch_avatars(self, pr):
        d = state_dir() / "avatars"
        for r in pr["reviewers"]:
            url, login = r.get("avatar"), r["login"]
            if not url or not LOGIN_RE.fullmatch(login) or (d / f"{login}.png").exists():
                continue
            if not url.startswith("https://avatars.githubusercontent.com/"):
                continue
            # curl, not urllib: it uses the system trust store, which a TLS-inspecting proxy's CA is added to.
            try:
                r = subprocess.run(["curl", "-sfL", "--max-time", "10", "--max-filesize", "524288",
                                    url + ("&" if "?" in url else "?") + "s=64"], capture_output=True, timeout=15)
            except (OSError, subprocess.TimeoutExpired):
                continue
            if r.returncode == 0 and r.stdout:
                write_atomic(d / f"{login}.png", r.stdout)

    # -- composing the page's state

    def pending_prompt(self, s, agent, records):
        """Fields that make a waiting item a pending confirmation the page can answer: the agent's terminal has a
        recorded permission request, and its screen shows that request's dialog now."""
        terms = [t for t in s["terminals"] if t.get("agent") and t.get("connected") and t.get("writable")]
        t = next((t for t in terms if t["pane"] == agent["pane"]), terms[0] if len(terms) == 1 else None)
        rec = t and records.get(t["handle"])
        if not rec:
            return {}
        try:
            d, _ = prompt_verdict(self.read_screen(t["handle"]), rec)
        except SourceError:
            d = None
        if not d:
            return {}
        return {"type": "prompt", "title": f"{rec['tool']} · {d['question']}", "detail": mask(prompt_needles(rec)[0], 300),
                "handle": t["handle"], "prompt": rec["id"], "answers": [x for x in ("approve", "deny") if option_for(d, x)]}

    def compose(self, sessions, root, cfg):
        now, mem = self.now(), self.mem
        prompts, phases = mem.setdefault("prompts", {}), mem.setdefault("phases", {})
        sticky, seen = mem.setdefault("sticky", {}), read_json(state_dir() / "seen.json", {}) or {}
        items, records = [], prompt_records()
        # Prompts and finished turns, per agent pane.
        for s in sessions.values():
            last_prompt = None
            for a in s["agents"]:
                pane = a["pane"] or s["id"]
                h = digest(a["prompt"]) if a["prompt"] else None
                old = prompts.get(pane)
                if h and (not old or old["h"] != h):
                    # A prompt first seen at collector start has no known time; stamping it "now" would mark
                    # every open item as read on a fresh install.
                    prompts[pane] = {"h": h, "at": iso(now) if old else None}
                    if old:
                        self.event({"at": iso(now), "kind": "prompt", "session": s["id"], "text": mask(a["prompt"], 160)})
                if (prompts.get(pane) or {}).get("at"):
                    last_prompt = max(filter(None, [last_prompt, prompts[pane]["at"]]))
                prev_phase = phases.get(pane)
                if prev_phase and prev_phase != a["state"]:
                    if a["state"] == "done":
                        self.event({"at": iso(now), "kind": "turn_done", "session": s["id"], "text": first_line(a["last"])})
                phases[pane] = a["state"]
                if a["state"] == "done" and a["last"]:
                    k = f"msg:{pane}:{digest(a['last'])}"
                    kind = None if k in sticky else classify(a["last"])
                    if kind and kind != "fyi":
                        sticky[k] = {"key": k, "type": kind, "session": s["id"], "title": first_line(a["last"]),
                                     "detail": a["last"][-600:], "at": iso(now), "source": "turn", "open": kind != "verify_ok"}
                        self.event({"at": iso(now), "kind": f"item_{kind}", "session": s["id"], "text": first_line(a["last"])})
                if a["state"] == "waiting":
                    items.append({"key": f"wait:{pane}:{a['since']}", "type": "permission", "session": s["id"],
                                  "title": "입력·권한 창에서 대기 중", "detail": a["detail"], "at": a["since"] or iso(now),
                                  "source": "orca", **self.pending_prompt(s, a, records)})
            s["last_prompt_at"] = last_prompt
        # Orchestration mail asking a coordinator something: open until someone replies. Unread mail stays; mail the
        # coordinator acked (its inbox loop acks at once, before the human has answered) stays for a day.
        coord_handles = {t["handle"] for s in sessions.values() if s["kind"] in ("orchestrator", "orchestration")
                         for t in s["terminals"]}
        coord_runs = {r["id"] for r in self.runs["runs"] if r.get("coordinator_handle") in coord_handles}  # the root's too
        by_handle = {t["handle"]: s["id"] for s in sessions.values() for t in s["terminals"]}
        answered, day_ago = answered_mail(self.fast["messages"]), iso(now - dt.timedelta(hours=24))
        for m in self.fast["messages"]:
            to = m.get("to_handle") or ""
            to_coord = to in coord_handles or (to.startswith("run:") and to[4:] in coord_runs)
            if (to_coord and m.get("type") in ("question", "escalation", "decision_gate") and m["id"] not in answered
                    and (not m.get("read") or (m.get("created_at") or "") >= day_ago)):
                items.append({"key": f"mail:{m['id']}", "type": "question", "session": by_handle.get(m.get("from_handle")),
                              "title": mask(m.get("subject"), 160), "detail": mask(m.get("body"), 600),
                              "at": m.get("created_at"), "source": "orca-mail"})
        items += decision_items(by_handle, root)
        for g in self.runs["gates"]:
            if g.get("status") not in ("resolved", "cancelled"):
                items.append({"key": f"gate:{g['id']}", "type": "approval", "session": root, "title": mask(g.get("question"), 160),
                              "detail": ", ".join(map(str, g.get("options") or [])), "at": g.get("created_at"), "source": "orca-gate"})
        # PRs: live items attached to the session that owns the PR.
        prs = []
        for k, sid in self.owner_of.items():
            det = (self.prs.get(k) or {}).get("detail")
            if not det:
                continue
            prs.append({f: det[f] for f in ("key", "number", "url", "title", "state", "ci", "decision", "reviewers", "updated")}
                       | {"session": sid})
            for it in pr_items(det, self.me, cfg):
                items.append({**it, "key": f"pr:{k}:{it['type']}", "session": sid, "at": det["updated"], "source": "github"})
        # Skill-side items and sync state.
        items += external_items(sticky) + sync_items(now)
        for it in sticky.values():
            if it.get("open"):
                items.append(it)
        dismissed, raw_keys = mem.setdefault("dismissed", {}), {it["key"] for it in items}
        items = [it for it in items if it["key"] not in dismissed]
        items.sort(key=lambda i: i.get("at") or "", reverse=True)
        # Unread and missed, per session.
        for i in items:
            s = sessions.get(i.get("session"))
            i["missed"] = bool(s and i["type"] in STICKY_TYPES and s.get("last_prompt_at")
                               and (i.get("at") or "") < s["last_prompt_at"])
        for s in sessions.values():
            mark = max(filter(None, [seen.get(s["id"]), s.get("last_prompt_at")]), default="")
            mine = [i for i in items if i.get("session") == s["id"]]
            s["unread"] = sum(1 for i in mine if (i.get("at") or "") > mark)
            s["missed"] = sum(i["missed"] for i in mine)
        # Keep the sticky store bounded: closed ones older than a week go.
        cutoff = iso(now - dt.timedelta(days=7))
        for k in [k for k, v in sticky.items() if not v.get("open") and (v.get("at") or "") < cutoff]:
            del sticky[k]
        for k in [k for k, at in dismissed.items() if at < cutoff and k not in raw_keys]:
            del dismissed[k]
        panes = {a["pane"] or s["id"] for s in sessions.values() for a in s["agents"]}
        for store in (prompts, phases):
            for k in [k for k in store if k not in panes]:
                del store[k]
        timeline = list(self.events)
        timeline += [{"at": m.get("created_at"), "kind": f"mail_{m.get('type')}", "session": by_handle.get(m.get("from_handle")),
                      "text": mask(m.get("subject"), 160), "read": bool(m.get("read"))} for m in self.fast["messages"]
                     if m.get("type") != "heartbeat"]  # one every 5 min per worker: it would bury everything else
        timeline.sort(key=lambda e: e.get("at") or "", reverse=True)
        return {"generated_at": iso(now), "root": root, "sessions": sorted(sessions.values(), key=lambda s: s.get("last_activity") or "", reverse=True),
                "runs": [{"id": r["id"], "objective": mask(r.get("objective"), 200), "updated_at": r.get("updated_at")} for r in self.runs["runs"]],
                "tasks": [{"id": t["id"], "run": t.get("run_id"), "title": mask(t.get("display_name") or t.get("task_title"), 160),
                           "status": t.get("status"), "deps": t.get("deps")} for t in self.runs["tasks"]],
                "prs": sorted(prs, key=lambda p: p.get("updated") or "", reverse=True), "items": items,
                "timeline": timeline[:300], "sources": self.sources, "me": self.me,
                "counts": {t: sum(1 for i in items if i["type"] == t) for t in {i["type"] for i in items}}}


def answered_mail(messages):
    """Ids of messages someone else replied to. `orca orchestration reply` gives the reply the thread_id of what it
    answers: the message's own id, or the thread the message was already in (`ask` opens a thread on itself). A reply
    is newer than its message and the inbox lists newest first, so a message on the page has its reply there too."""
    by_thread = collections.defaultdict(list)
    for r in messages:
        if r.get("thread_id"):
            by_thread[r["thread_id"]].append(r)
    return {m["id"] for m in messages for t in {m["id"], m.get("thread_id")} - {None} for r in by_thread[t]
            if r["id"] != m["id"] and r.get("from_handle") != m.get("from_handle")
            and (r.get("created_at") or "") >= (m.get("created_at") or "")}


def decision_items(by_handle, root):
    """Open decisions from `orch decide`, on the session whose terminal registered them (else the root)."""
    out = []
    for d in (read_json(state_dir() / "decisions.json", {}) or {}).get("decisions", {}).values():
        if d.get("status") != "open":
            continue
        sid = by_handle.get(d.get("handle"))
        out.append({"key": f"decision:{d['id']}", "type": "decision", "session": sid or root, "handle": d.get("handle") if sid else None,
                    "decision": d["id"], "title": mask(d.get("title"), 200), "detail": first_line(d.get("body")),
                    "body": mask(d.get("body"), DECISION_BODY_MAX), "options": d.get("options") or [],
                    "recommend": d.get("recommend"), "url": d.get("link"), "at": d.get("created_at"), "source": "orch decide"})
    return out


def external_items(sticky):
    """Items the orchestrator skill added with `orch-dash inbox add` (or by appending the same line)."""
    items = {}
    for row in read_lines(state_dir() / "inbox-external.jsonl"):
        key = f"ext:{row.get('key')}"
        if row.get("op") == "resolve":
            items.pop(key, None)
        elif row.get("op") == "add" and row.get("type"):
            items[key] = {"key": key, "type": row["type"], "session": row.get("session"), "title": mask(row.get("title"), 200),
                          "url": row.get("url"), "command": mask(row.get("command"), 500), "at": row.get("at"),
                          "source": row.get("source") or "skill"}
    return list(items.values())


def sync_items(now):
    """Skill sync health, written by the platform side's skills-sync."""
    base, out = state_dir().parent, []
    sync = read_json(base / "sync.json")
    if isinstance(sync, dict):
        at, every = parse(sync.get("at")), sync.get("interval") or 900
        if sync.get("ok") is False or (at and (now - at).total_seconds() > 2 * every):
            out.append({"key": f"sync:{sync.get('at')}", "type": "sync_stalled", "session": None, "at": sync.get("at"),
                        "title": "스킬 동기화 멈춤", "detail": mask(sync.get("message"), 300), "source": "skills-sync"})
    pending = read_json(base / "reload-pending.json")
    if isinstance(pending, dict):
        for handle, why in (pending.get("failed") or {}).items():
            out.append({"key": f"reload:{handle}:{pending.get('since')}", "type": "reload_pending", "session": None,
                        "terminal": handle, "at": pending.get("since"), "source": "skills-sync",
                        "title": f"reload 못 보냄: {mask((why or {}).get('title'), 80)}", "detail": mask((why or {}).get("reason"), 200)})
    return out


# ---------------------------------------------------------------- actions from the page and the CLI

def load_sync():
    """The platform's idle-checked sender (scripts/sync.py at the repository root), or None when absent."""
    root = str(pathlib.Path(__file__).resolve().parents[3] / "scripts")
    if root not in sys.path:
        sys.path.append(root)
    try:
        import sync
        return sync if hasattr(sync, "try_send") else None
    except ImportError:
        return None


def send(session_id, text, handle=None, sender=None):
    """Type one line into a fleet session's agent terminal. Returns (status, body) for the HTTP reply.
    Only agent terminals of sessions in the current state are reachable; every attempt is logged."""
    state = read_json(state_dir() / "state.json", {}) or {}
    s = next((x for x in state.get("sessions") or [] if x["id"] == session_id), None)
    terms = [t for t in (s or {}).get("terminals") or [] if t.get("agent") and t.get("connected") and t.get("writable")]
    handles = [t["handle"] for t in terms]
    if handle is None and len(handles) == 1:
        handle = handles[0]
    row = {"at": iso(utcnow()), "session": session_id, "handle": handle, "text": mask(text, 500)}
    if not s or handle not in handles:
        code, out = 403, {"ok": False, "reason": "not a connected agent terminal of a fleet session"}
    elif not text.strip() or "\n" in text or "\r" in text or len(text) > 2000:
        code, out = 400, {"ok": False, "reason": "one non-empty line, at most 2000 characters"}
    else:
        sender = sender or load_sync()
        if sender is None:
            code, out = 200, {"ok": False, "reason": "safety check unavailable (scripts/sync.py not found)", "fallback": "copy"}
        else:
            ok, reason = sender.try_send(handle, text)
            # This one reason means the line was typed and Enter pressed: a retry would send it twice.
            typed = ok or (reason or "").startswith("sent, but")
            code, out = 200, {"ok": ok, "reason": reason, "typed": typed, "fallback": None if typed else "copy"}
    append_line(state_dir() / "sends.jsonl", {**row, **out, "code": code})
    return code, out


def answer_prompt(session_id, prompt_id, action, sender=None):
    """Answer a pending permission dialog the way the human chose on the page: approve, deny, or open (switch Orca
    to that terminal). Returns (status, body) for the HTTP reply; every attempt is logged."""
    state = read_json(state_dir() / "state.json", {}) or {}
    s = next((x for x in state.get("sessions") or [] if x["id"] == session_id), None)
    handles = {t["handle"] for t in (s or {}).get("terminals") or [] if t.get("agent") and t.get("connected") and t.get("writable")}
    rec = next((r for h, r in prompt_records().items() if h in handles and r.get("id") == prompt_id), None)
    row = {"at": iso(utcnow()), "session": session_id, "handle": rec and rec["handle"], "text": f"prompt {action}"}
    if action not in ("approve", "deny", "open"):
        code, out = 400, {"ok": False, "reason": "action is approve, deny or open"}
    elif not rec:
        code, out = 409, {"ok": False, "reason": "no pending prompt with that id in this session's agent terminals"}
    elif action == "open":
        try:
            orca("terminal", "switch", "--terminal", rec["handle"])
            code, out = 200, {"ok": True, "reason": None}
        except SourceError as e:
            code, out = 200, {"ok": False, "reason": str(e)}
    else:
        sender = sender or load_sync()
        reason, key = press_option(sender, rec, action) if sender else ("safety check unavailable (scripts/sync.py not found)", None)
        code, out = 200, {"ok": reason is None, "reason": reason, "typed": key is not None, "key": key,
                          "fallback": None if key else "open"}
        if key:
            (state_dir() / "prompts" / f"{rec['handle']}.json").unlink(missing_ok=True)
    append_line(state_dir() / "sends.jsonl", {**row, **out, "code": code})
    return code, out


def press_option(sync, rec, action):
    """(reason, key). Under the lock the reload broadcast and the chat box share, the screen is read twice and both
    reads must show the same dialog for the recorded request; only then is the one digit pressed.
    reason is None once the dialog has left the screen; key is the digit pressed, or None when nothing was."""
    handle, key = rec["handle"], None
    with sync.locked(10) as got:
        if not got:
            return "a sync or broadcast is running; try again shortly", None
        try:
            first = sync.screen(handle)[0]
            time.sleep(sync.SETTLE_S)
            second = sync.screen(handle)[0]
            if not second:
                return "not a connected, writable Claude terminal", None
            d, why = prompt_verdict(second, rec)
            if not d:
                return why, None
            # Only the dialog is compared: above it the pending tool's ⏺ blinks between reads (measured).
            if d != parse_dialog(first):
                return "the dialog changed between two reads (someone may be answering it)", None
            digit = option_for(d, action)
            if not digit:
                return ("no one-time Yes option: every Yes saves a rule or changes the mode, answer in the terminal"
                        if action == "approve" else "no No option on the dialog, answer in the terminal"), None
            sync.orca("terminal", "send", "--terminal", handle, "--text", digit)  # a digit picks the option, no Enter
            key = digit
            time.sleep(sync.CONFIRM_S)
            after = sync.screen(handle)[0]
        except sync.Stop as e:
            return str(e), key
    if prompt_verdict(after, rec)[0]:
        return "sent, but the dialog is still on screen", key
    return None, key


def mark_seen(session_id):
    path = state_dir() / "seen.json"
    seen = read_json(path, {}) or {}
    seen[str(session_id)] = iso(utcnow())
    write_atomic(path, json.dumps(seen))


def dismiss(fleet, key):
    with fleet.lock:
        fleet.mem.setdefault("dismissed", {})[key] = iso(utcnow())
        st = fleet.mem.get("sticky", {}).get(key)
        if st:
            st["open"] = False
        fleet.save(**{"memory.json": json.dumps(fleet.mem, ensure_ascii=False)})


def inbox_add(kind, title, session=None, url=None, command=None, key=None, source="orchestrator"):
    row = {"v": 1, "op": "add", "key": key or digest(kind, title, session), "type": kind, "title": title[:200],
           "session": session, "url": url, "command": command, "at": iso(utcnow()), "source": source}
    append_line(state_dir() / "inbox-external.jsonl", row)
    return row


def inbox_resolve(key):
    row = {"v": 1, "op": "resolve", "key": key, "at": iso(utcnow())}
    append_line(state_dir() / "inbox-external.jsonl", row)
    return row


# ---------------------------------------------------------------- decisions the coordinator waits on the human for

DECISION_BODY_MAX = 4000
DECISION_KEEP_S = 7 * 86400  # closed decisions are kept this long, then dropped from the file


def change_decisions(change):
    """Run change(store) on decisions.json under a lock and replace the file whole; returns what change returns."""
    path = state_dir() / "decisions.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with (state_dir() / "decisions.lock").open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        store = read_json(path, {}) or {}
        out = change(store)
        write_atomic(path, json.dumps(store, ensure_ascii=False, indent=1) + "\n")
        return out


def decision_add(title, body="", options=(), recommend=None, link=None, handle=None):
    """Register a decision; options are "label::description". The label is what the page sends back, so one line."""
    one_line = lambda v, n: mask(" ".join(v.split()), n)
    opts = []
    for o in options:
        label, _, desc = o.partition("::")
        if not label.strip():
            raise ValueError(f"--option {o!r} has no label before '::'")
        opts.append({"label": one_line(label, 120), "description": one_line(desc, 300)})
    if recommend is not None and not 1 <= recommend <= len(opts):
        raise ValueError(f"--recommend is an option number from 1 to {len(opts)}" if opts else "--recommend needs --option")
    if not title.strip():
        raise ValueError("--title is empty")
    now = utcnow()

    def add(store):
        n = store.get("next", 1)
        d = {"id": f"d{n}", "title": one_line(title, 200), "body": mask(body.strip(), DECISION_BODY_MAX), "options": opts,
             "recommend": recommend, "link": mask(link, 500), "handle": handle, "created_at": iso(now), "status": "open"}
        cutoff = iso(now - dt.timedelta(seconds=DECISION_KEEP_S))
        kept = {k: v for k, v in store.get("decisions", {}).items() if v.get("status") == "open" or (v.get("closed_at") or "") >= cutoff}
        store.update(next=n + 1, decisions={**kept, d["id"]: d})
        return d
    return change_decisions(add)


def decision_close(did, status, answer=None):
    """Mark an open decision done (answered) or dropped (no longer needed)."""
    def close(store):
        d = store.get("decisions", {}).get(did)
        if not d:
            raise ValueError(f"no decision {did}")
        if d.get("status") != "open":
            raise ValueError(f"{did} is already {d.get('status')}")
        d.update(status=status, answer=mask(answer, 500), closed_at=iso(utcnow()))
        return d
    return change_decisions(close)


def open_decisions():
    return [d for d in (read_json(state_dir() / "decisions.json", {}) or {}).get("decisions", {}).values() if d.get("status") == "open"]


# ---------------------------------------------------------------- adoption into Orca's own lineage (opt-in)

def adopt(apply=False, undo=None):
    """Print each session's proposed Orca parent. With --apply (and config adopt.write_orca_parent), set it on
    sessions that have no parent yet, logging the previous value; --undo restores logged values."""
    log_path = state_dir() / "adoption.jsonl"
    if undo:
        latest = {}
        for row in read_lines(log_path):
            latest[row.get("worktree")] = row
        done = 0
        for wt, row in latest.items():
            if row.get("op") != "set" or undo not in ("all", wt):
                continue
            before = row.get("before")
            args = ["--parent-worktree", f"id:{before}"] if before else ["--no-parent"]
            orca("worktree", "set", "--worktree", f"id:{wt}", *args)
            append_line(log_path, {"at": iso(utcnow()), "op": "undo", "worktree": wt, "restored": before})
            done += 1
        print(f"restored {done} worktree(s)")
        return 0
    cfg = config()
    sessions, root = build_sessions(fetch_fast(), fetch_runs(), cfg)
    if not root:
        print("no orchestrator found: set root_worktree in the config, or start a Run from the orchestrator")
        return 1
    plan = [(s, s["parent"]) for s in sessions.values()
            if s["parent"] and s["parent"] != s["id"] and not s["orca_parent"]]
    for s, parent in plan:
        print(f"{'set ' if apply else 'would set'} {s['name'][:40]:40}  parent -> {sessions[parent]['name'][:40]}")
    kept = sum(1 for s in sessions.values() if s["orca_parent"])
    print(f"{len(plan)} to adopt, {kept} already have a parent (left as they are)")
    if not apply:
        return 0
    if not (cfg.get("adopt") or {}).get("write_orca_parent"):
        print(f"not written: set adopt.write_orca_parent to true in {config_path()} first")
        return 1
    for s, parent in plan:
        append_line(log_path, {"at": iso(utcnow()), "op": "set", "worktree": s["id"], "before": s["orca_parent"],
                               "after": parent})
        orca("worktree", "set", "--worktree", f"id:{s['id']}", "--parent-worktree", f"id:{parent}")
    print(f"wrote {len(plan)}; undo with: orch-dash adopt --undo all")
    return 0
