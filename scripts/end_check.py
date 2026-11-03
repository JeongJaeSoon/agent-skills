#!/usr/bin/env python3
"""Whether this card may go: end-session §4's state table (and §7's for a worker, §8's for a dispatched card), read
from real output and printed as one verdict.

Usage:
  skills-sync end-check [--worker --outcome succeeded|failed | --card] [--brief <file> | --keep | --no-keep]
                        [--ticket-done] [--dispatch <id>] [--apply]

Verdicts:
  remove          the Remove row holds: `orca worktree rm --worktree current` (after moving build caches and
                  regenerated skill copies to the trash)
  close-terminal  the card stays (main worktree, or another agent works on it): only this terminal goes
  keep           something holds the card: a failed outcome, an open PR, a question waiting, a dirty tree, a KEEP row,
                  or a fact this could not read. Not knowing is keeping: nothing is removed on a guess.
  ask             (no --worker or --card) tracked changes, unexpected untracked files or unpushed work: the human picks

Facts and where they come from: the tree and the branch (`git fetch`, `git status`, `git diff origin/main...HEAD`),
its PRs (`gh pr list --head <branch>`), the card (`orca worktree current`, `orca terminal list --worktree current`),
questions this terminal sent that nobody answered yet (`orca orchestration inbox`: type `question`, from
$ORCA_TERMINAL_HANDLE, no reply in its thread). The KEEP row is read from --brief, or stated with --keep or --no-keep.
A linked ticket in --card mode needs --ticket-done once use-tracker shows it completed (its state is not read here).
`orch decide` questions are a lead's and not read.

--apply carries the verdict out. remove and close-terminal for a worker first ask Orca whether its Dispatch settled
(`worker-show`: status completed or failed): worker_done must have arrived before the card goes (end-session §7).
keep for a worker starts `skills-sync close-setup <card>` in the background. Nothing is applied on keep or ask
otherwise.

Exit codes: 0 the verdict printed (and applied); 2 usage; 3 --apply refused: the Dispatch has not settled or could
not be read; 4 an applied command failed (its output is printed).
"""
import datetime, json, math, os, pathlib, re, subprocess, sys, time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills" / "orchestrate" / "scripts"))
ORCA = os.environ.get("SKILLS_SYNC_ORCA", "orca")
GH = os.environ.get("SKILLS_SYNC_GH", "gh")
STATE = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE", "~/.local/state/agent-skills")).expanduser()
CACHE_DIRS = ("__pycache__", ".pytest_cache", "node_modules/.cache")
SETTLED = ("completed", "failed")
INBOX_LIMIT = "2000"
ME = "skills-sync end-check"


class Usage(Exception):
    pass


def run(cmd, cwd=None, timeout=60):
    """(returncode, stdout, stderr); returncode None when it could not run."""
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, "", str(e)
    return p.returncode, p.stdout, p.stderr


def orca_json(*args):
    """The `result` of `orca <args> --json`, or None when it failed or said nothing readable."""
    code, out, _ = run([ORCA, *args, "--json"], timeout=30)
    if code != 0:
        return None
    try:
        doc = json.loads(out)
        res = doc.get("result") if doc.get("ok") is not False else None
    except (ValueError, AttributeError):
        return None
    return res if isinstance(res, dict) else None


def removable(path, tracked_skills):
    """A build cache, or a regenerated skill copy (an `.agents/skills/<name>/` whose `.claude/skills/<name>` is
    tracked): Orca rebuilds the one and the other is cache, so they go to the trash before `orca worktree rm`."""
    p = path.rstrip("/")
    if any(p == d or p.endswith("/" + d) or f"/{d}/" in f"/{p}/" for d in CACHE_DIRS):
        return True
    m = re.fullmatch(r"\.agents/skills/([^/]+)", p)
    return bool(m and m.group(1) in tracked_skills)


def git_facts(cwd):
    """The tree, branch and PR facts; a value is None when its command failed."""
    f = {}
    fetched = run(["git", "fetch", "-q", "origin", "main"], cwd)[0] == 0
    code, out, _ = run(["git", "status", "--porcelain", "--untracked-files=no"], cwd)
    f["tracked"] = out.splitlines() if code == 0 else None
    code, out, _ = run(["git", "status", "--porcelain", "--ignored", "--untracked-files=normal"], cwd)
    code2, skills, _ = run(["git", "ls-files", "--directory", ".claude/skills"], cwd)
    if code == 0 and code2 == 0:
        tracked_skills = {pathlib.PurePath(s).parts[2] for s in skills.splitlines() if len(pathlib.PurePath(s).parts) > 2}
        left = [l[3:] for l in out.splitlines() if l[:2] in ("??", "!!")]
        f["caches"] = [p for p in left if removable(p, tracked_skills)]
        f["untracked"] = [p for p in left if not removable(p, tracked_skills)]
    else:
        f["caches"] = f["untracked"] = None
    code, branch, _ = run(["git", "branch", "--show-current"], cwd)
    branch = branch.strip() if code == 0 else ""
    f["branch"] = branch or None
    prs = {}
    for state in ("open", "merged"):
        code, out, _ = run([GH, "pr", "list", "--head", branch, "--state", state, "--json", "number,url,headRefOid,baseRefName"],
                           cwd) if branch else (None, "", "")
        try:
            got = json.loads(out) if code == 0 else None
        except ValueError:
            got = None
        prs[state] = got if isinstance(got, list) and all(isinstance(p, dict) for p in got) else None
    f["open_prs"] = prs["open"]
    code, head, _ = run(["git", "rev-parse", "HEAD"], cwd)
    head = head.strip() if code == 0 else None
    if not fetched or head is None or prs["merged"] is None or prs["open"] is None:
        f["on_main"] = None
    elif any(isinstance(p, dict) and p.get("headRefOid") == head and p.get("baseRefName") == "main"
             for p in prs["merged"]):
        f["on_main"] = True  # after a squash merge the branch's diff still shows; the PR merged into main at HEAD
    else:
        code, out, _ = run(["git", "diff", "--stat", "origin/main...HEAD"], cwd)
        f["on_main"] = (not out.strip()) if code == 0 else None
    return f


def card_facts(me, mode="card", dispatch=None):
    f = {}
    cur = orca_json("worktree", "current")
    wt = (cur or {}).get("worktree")
    wt = wt if isinstance(wt, dict) else {}
    f["main"] = wt["isMainWorktree"] if isinstance(wt.get("isMainWorktree"), bool) else None
    f["path"] = wt.get("path") if isinstance(wt.get("path"), str) else None
    items = wt.get("linkedItems")
    if not wt:
        f["tickets"] = None
    elif not isinstance(items, list):
        print(f"[{ME}] warning: Orca's worktree has no linkedItems list ({items!r}); its ticket is unknown",
              file=sys.stderr)
        f["tickets"] = None
    else:
        f["tickets"] = items + [wt[k] for k in ("linkedLinearIssue", "linkedIssue", "linkedWorkItem") if wt.get(k)]
    terms = orca_json("terminal", "list", "--worktree", "current")
    rows = (terms or {}).get("terminals")
    # agentIdentity is an agent's name, or null for a shell; any other value is unread.
    if isinstance(rows, list) and me and all(isinstance(t, dict) and "agentIdentity" in t
                                             and (t["agentIdentity"] is None
                                                  or isinstance(t["agentIdentity"], str) and t["agentIdentity"])
                                             for t in rows):
        f["other_agents"] = [t.get("handle") for t in rows if t.get("agentIdentity") and t.get("handle") != me]
    else:
        if isinstance(rows, list):
            print(f"[{ME}] warning: an Orca terminal row has no agentIdentity; other agents on the card are unknown",
                  file=sys.stderr)
        f["other_agents"] = None
    # Every terminal still on the card asks for it, not only this one; a closed terminal's handle is gone from Orca.
    whole = isinstance(rows, list) and all(isinstance(t, dict) and isinstance(t.get("handle"), str) and t["handle"]
                                           for t in rows)
    card_handles = {t["handle"] for t in rows} if whole else None  # a row with no handle: its questions are unknown
    senders = {me, *(card_handles or ())} | ({f"dispatch:{dispatch}"} if dispatch else set())
    f["questions"] = waiting_questions(senders, wt.get("createdAt"), me) if me and card_handles is not None else None
    if mode == "worker" and not dispatch:
        f["questions"] = None  # its questions may come from dispatch:<id>, which is not known
    return f


ISO_SECONDS = re.compile(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d")


def when(value, ms=False):
    """Epoch seconds of an ISO time string with seconds (`Z` or none: UTC), or with ms=True of an epoch-milliseconds
    number from 2000 up to a minute from now; None for anything else."""
    if ms:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        return value / 1000 if ok and 946684800 <= value / 1000 <= time.time() + 60 else None
    if not isinstance(value, str) or not ISO_SECONDS.match(value):
        return None
    try:
        t = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)).timestamp()


def inbox_rows(res):
    """The rows when every one is whole (unique non-empty string id, type and from_handle; a string or no thread_id
    and to_handle; a created_at to the second) and the paging fields are readable; else None. Also whether more
    pages follow."""
    msgs, page = res.get("messages"), res.get("page", {})
    flags = [res.get("hasMore"), res.get("nextCursor")] + ([page.get("hasMore"), page.get("nextCursor")]
                                                           if isinstance(page, dict) else [])
    if not isinstance(msgs, list) or not isinstance(page, dict) or any(
            f is not None and not isinstance(f, (bool, str)) for f in flags):
        return None, None
    text = lambda m, k: isinstance(m.get(k), str) and m[k]  # noqa: E731
    if not all(isinstance(m, dict) and text(m, "id") and text(m, "type") and text(m, "from_handle")
               and all(m.get(k) is None or isinstance(m[k], str) for k in ("thread_id", "to_handle"))
               and when(m.get("created_at")) is not None for m in msgs):
        return None, None
    if len({m["id"] for m in msgs}) != len(msgs):
        return None, None
    count = res.get("count")
    if not isinstance(count, int) or isinstance(count, bool) or count != len(msgs):
        return None, None
    return msgs, any(flags)


def waiting_questions(senders, since=None, me=None):
    """Ids of `question` mail one of these senders sent that nobody answered, or None when the inbox could not be
    read whole. A page cut short still covers the card when its oldest row is more than a second older than `since`
    (the card's creation): the card's questions, and their replies, all came after it. A question counts as answered
    only by a `status` in its thread, addressed to the asker by anyone else, and later. `me` and the `dispatch:`
    handles in `senders` are one asker; each other card terminal is its own."""
    import orca_settings

    def call(*argv):
        res = orca_json(*argv)
        if res is None:
            raise RuntimeError("no answer")
        return res
    try:
        res = orca_settings.with_full(call, "orchestration", "inbox", "--limit", INBOX_LIMIT)
    except RuntimeError:
        return None
    msgs, more = inbox_rows(res)
    if msgs is None:
        print(f"[{ME}] warning: Orca's inbox has a row or paging field this cannot read whole; questions waiting "
              "are unknown", file=sys.stderr)
        return None
    start = when(since, ms=True)
    covers = bool(msgs) and start is not None and min(when(m["created_at"]) for m in msgs) < start - 1
    if (len(msgs) >= int(INBOX_LIMIT) or more) and not covers:
        print(f"[{ME}] warning: the inbox has more than one page and its oldest row is not older than this card; "
              "questions waiting are unknown", file=sys.stderr)
        return None

    def later(r, q):
        # Within one second the order on the page decides: Orca lists newest first.
        tr, tq = when(r["created_at"]), when(q["created_at"])
        return tr > tq or tr == tq and msgs.index(r) < msgs.index(q)

    own = {me} | {s for s in senders if s.startswith("dispatch:")}

    def answered(q):
        # The asker is this session under any of its handles, or one other terminal of the card.
        asker = own if q["from_handle"] in own else {q["from_handle"]}
        threads = {q["id"], q.get("thread_id")} - {None}
        return any(r["type"] == "status" and r.get("thread_id") in threads and r["from_handle"] not in asker
                   and r.get("to_handle") in asker and later(r, q) for r in msgs)
    return [m["id"] for m in msgs if m["from_handle"] in senders and m["type"] == "question" and not answered(m)]


def keep_row(opts):
    """True or False for the brief's KEEP row, None when nothing said."""
    if opts.get("--keep"):
        return True
    if opts.get("--no-keep"):
        return False
    if opts.get("--brief"):
        import card
        try:
            text = card.FENCE.sub("", pathlib.Path(opts["--brief"]).expanduser().read_text())
        except OSError:
            return None
        return card.row(text, "KEEP") is not None
    return None


def judge(f, mode, outcome=None, keep=None, ticket_done=False):
    """(verdict, [reasons]) from the facts. mode: worker | card | user. Every unread fact keeps the card."""
    unknown = [k for k in ("tracked", "untracked", "on_main", "open_prs", "main", "other_agents", "questions")
               if f.get(k) is None]
    if mode == "worker" and outcome not in ("succeeded", "failed"):
        unknown.append("outcome (pass --outcome)")
    if mode != "user" and keep is None:
        unknown.append("KEEP row (pass --brief, --keep or --no-keep)")
    holds = []
    if mode == "worker" and outcome == "failed":
        holds.append("outcome failed: the session above retries on this card")
    if f.get("open_prs"):
        holds.append("open PR " + ", ".join(str(p.get("url") or p.get("number")) for p in f["open_prs"]))
    if f.get("questions"):
        holds.append("question waiting: " + ", ".join(map(str, f["questions"])))
    if keep:
        holds.append("the brief has a KEEP row")
    dirty = []
    if f.get("tracked"):
        dirty.append("tracked changes: " + "; ".join(f["tracked"][:5]))
    if f.get("untracked"):
        dirty.append("untracked files that are neither build caches nor regenerated copies: "
                     + "; ".join(f["untracked"][:5]))
    if f.get("on_main") is False:
        dirty.append("the branch's work is not on origin/main and no PR of it merged")
    if mode == "card" and f.get("tickets") and not ticket_done:
        holds.append("linked ticket: its state is not read here; pass --ticket-done once it is completed")
    if mode == "card" and f.get("tickets") is None:
        unknown.append("linked ticket")
    if f.get("archive_hook", False) is None:
        unknown.append("orca.yaml (its archive hook)")
    reasons = holds + dirty + [f"cannot read: {u}" for u in unknown]
    if dirty and mode == "user" and not holds:
        return "ask", reasons
    if reasons:
        return "keep", reasons
    if f["main"] or f["other_agents"]:
        why = "isMainWorktree" if f["main"] else "other agents on the card: " + ", ".join(map(str, f["other_agents"]))
        return "close-terminal", [why]
    return "remove", ["the Remove row holds"]


def parse(argv):
    opts, valued, flags = {}, {"--outcome", "--brief", "--dispatch"}, {"--worker", "--card", "--keep", "--no-keep",
                                                                       "--ticket-done", "--apply", "--json"}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in valued and i + 1 < len(argv):
            opts[a], i = argv[i + 1], i + 2
        elif a in flags:
            opts[a], i = True, i + 1
        else:
            raise Usage(f"unexpected argument {a!r}")
    if opts.get("--worker") and opts.get("--card"):
        raise Usage("--worker and --card exclude each other")
    if sum(bool(opts.get(k)) for k in ("--brief", "--keep", "--no-keep")) > 1:
        raise Usage("--brief, --keep and --no-keep exclude each other")
    if opts.get("--outcome") not in (None, "succeeded", "failed"):
        raise Usage("--outcome is succeeded or failed")
    return opts


def own_dispatch_id(given):
    if given:
        return given
    import orca_settings
    own = orca_settings.own_dispatch(ORCA, failed="unknown")
    return own.get("dispatchId") if isinstance(own, dict) else None


def dispatch_settled(dispatch_id):
    """(True, status) when Orca says the Dispatch settled; (False, why) otherwise."""
    if not dispatch_id:
        return False, "this terminal's Dispatch could not be found (pass --dispatch <id>)"
    res = orca_json("orchestration", "worker-show", "--dispatch", dispatch_id, "--full")
    status = ((res or {}).get("dispatch") or {}).get("status") if isinstance((res or {}).get("dispatch"), dict) else None
    if status in SETTLED:
        return True, status
    if status is None:
        print(f"[{ME}] warning: worker-show for {dispatch_id} names no dispatch.status", file=sys.stderr)
    return False, f"Dispatch {dispatch_id} is {status or 'unknown'}: send worker_done and wait for its \"ok\": true"


def archive_hook(path):
    """Whether the card's orca.yaml has an archive hook; None when the file exists but cannot be read."""
    try:
        text = (pathlib.Path(path) / "orca.yaml").read_text()
    except FileNotFoundError:
        return False
    except (OSError, TypeError, UnicodeDecodeError):
        return None
    return re.search(r"(?m)^\s*archive\s*:", text) is not None


def apply(verdict, f, mode, opts, me):
    if mode == "worker" and verdict in ("remove", "close-terminal"):
        ok, why = dispatch_settled(opts.get("--dispatch"))
        if not ok:
            print(f"[{ME}] not applied: {why}", file=sys.stderr)
            return 3
    if verdict == "remove":
        hooks = archive_hook(f["path"])
        if hooks is None:
            print(f"[{ME}] not applied: {f['path']}/orca.yaml could not be read, so its archive hook is unknown",
                  file=sys.stderr)
            return 3
        if f["caches"]:
            sys.path.insert(0, str(REPO / "skills" / "reap-resources" / "scripts"))
            import reap
            try:
                reap.trash(*(str(pathlib.Path(f["path"] or ".") / p) for p in f["caches"]))
            except (RuntimeError, OSError) as e:
                print(f"[{ME}] moving build caches to the trash failed: {e}", file=sys.stderr)
                return 4
        cmd = [ORCA, "worktree", "rm", "--worktree", "current", *(["--run-hooks"] if hooks else []), "--json"]
    elif verdict == "close-terminal":
        cmd = [ORCA, "terminal", "close", "--terminal", me, "--json"]
    elif verdict == "keep" and mode == "worker" and f.get("path"):
        log = STATE / "close-setup" / f"{pathlib.Path(f['path']).name}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a") as out:
            subprocess.Popen([sys.executable, str(REPO / "scripts" / "sync.py"), "close-setup", f["path"]],
                             stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        print(f"[{ME}] close-setup started in the background; its log: {log}")
        return 0
    else:
        return 0
    code, out, err = run(cmd, timeout=120)
    print(out.strip() or err.strip())
    return 0 if code == 0 else 4


def main(argv):
    try:
        opts = parse(argv)
    except Usage as e:
        print(f"{e}\n{__doc__}", file=sys.stderr)
        return 2
    mode = "worker" if opts.get("--worker") else "card" if opts.get("--card") else "user"
    me = os.environ.get("ORCA_TERMINAL_HANDLE")
    if mode == "worker":
        opts["--dispatch"] = own_dispatch_id(opts.get("--dispatch"))
    card = card_facts(me, mode, opts.get("--dispatch"))
    # The git facts come from the card Orca would remove, not from wherever this ran.
    git = git_facts(card["path"]) if card["path"] else dict.fromkeys(("tracked", "untracked", "caches", "on_main",
                                                                       "open_prs", "branch"))
    facts = {**git, **card, "archive_hook": archive_hook(card["path"]) if card["path"] else None}
    verdict, reasons = judge(facts, mode, opts.get("--outcome"), keep_row(opts), bool(opts.get("--ticket-done")))
    print(json.dumps({"verdict": verdict, "mode": mode, "reasons": reasons, "facts": facts}, ensure_ascii=False,
                     indent=1))
    return apply(verdict, facts, mode, opts, me) if opts.get("--apply") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
