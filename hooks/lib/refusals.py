"""The refused-act ledger: an act the auto mode classifier (with a category) or the human refused is not done again by
any session until the human clears it.

What counts as a refusal is read from the tool result text Claude Code writes: the classifier's
"Permission for this action was denied by the Claude Code auto mode classifier. Reason: [<category>]" and the human's
"The user doesn't want to proceed with this tool use." / "Permission for this tool use was denied. The tool use was
rejected". A hook's block, a `<tool_use_error>`, and a classifier refusal with no category (no verdict, or judged
dangerous with no explanation) are not recorded: each has its own documented way forward.

An act is a normalized (verb, target): a deleted path by its real parent and its own name (a symlink is itself), a
written file by its real path, a PR as `host/owner/repo#n`, a branch as `host/owner/repo:branch`. Deleting one path
counts whichever command does it (`rm -rf`, `git worktree remove`, `orca worktree rm`), and deleting a directory that
holds a refused deletion's target matches it too. Only the shapes below are read; a call that changes something this
module cannot pin to a target (an unknown command, a PR gh cannot name, `git push --all`, a relative path after a
`cd` in a subshell) is `unknown`: nothing is recorded or matched for that part, and the hook says so.

$AGENT_SKILLS_STATE/refusals.jsonl holds per line the time, the event (tool use) id, the session, the Orca terminal,
the Run, the verb and the target. Never the command text or other argument values: they can hold a secret.
Every write takes an fcntl lock on refusals.lock. Only `orch refusal clear <id>`, typed by the human as a `!` line,
clears a refusal (guard.deny.owner_only refuses it from an agent). A refusal cleared over 30 days ago shrinks to a
`done` row that keeps its id, so a transcript still holding it does not record it again. The first row (`start`)
dates the ledger: a transcript's refusals from before it are not collected.
Scripts that run commands themselves call `blocked(acts(...)[0])` before they run them.
"""
import contextlib, datetime as dt, fcntl, json, os, re, subprocess, time

from lib import config, danger, shell

KEY = "guard.refusal_registry"
DEFAULT = "deny"
KEEP_DAYS = 30
TAIL_BYTES = 1 << 20

CLASSIFIER = re.compile(r"\s*Permission for this action was denied by the Claude Code auto mode classifier\b")
CATEGORY = re.compile(r"\s*Permission for this action was denied by the Claude Code auto mode classifier\. "
                      r"Reason: \[([^\]\n]+)\]")
NO_VERDICT = re.compile(r"\s*The server-side auto mode classifier gave no verdict\b")
HUMAN = re.compile(r"\s*(?:The user doesn't want to proceed with this tool use\."
                   r"|Permission for this tool use was denied\. The tool use was rejected)")
# PermissionDenied's `reason` is the classifier's own reason: a category comes first, in brackets.
REASON_CATEGORY = re.compile(r"\s*\[([^\]\n]+)\]")

# Tools that change nothing: a refused one is not recorded, and nothing blocks them.
READ_ONLY = {"Read", "Grep", "Glob", "LS", "WebFetch", "WebSearch", "ToolSearch", "TaskOutput", "TaskList", "TaskGet",
             "ListMcpResourcesTool", "ReadMcpResourceTool"}
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
GH_REPO = {"-R", "--repo"}
MISSING = "no"  # an `acts` note about the tool input itself, not a shape it cannot read
PR_URL = re.compile(r"https?://([^/\s]+)/([\w.-]+)/([\w.-]+)/pull/(\d+)")
REMOTE = re.compile(r"(?:^[\w+.-]+://(?:[^@/]+@)?|^[^@/:]+@|^)([^/:]+)[:/]+([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
API_MERGE = re.compile(r"^/?repos/([\w.-]+)/([\w.-]+)/pulls/(\d+)/merge$")
HOME_VAR = re.compile(r"\$\{HOME\}|\$HOME(?!\w)")
PUSH_VALUED = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}

# Every lookup a hook makes shares one deadline (set_deadline), so the hook ends before Claude Code's timeout.
_deadline = None


class Unknown(Exception):
    """The call changes something this module cannot pin to one target."""


def set_deadline(seconds):
    global _deadline
    _deadline = time.monotonic() + seconds


def left(cap):
    if _deadline is None:
        return cap
    rest = _deadline - time.monotonic()
    if rest < 0.3:
        raise TimeoutError("refused-act ledger ran out of time")
    return min(cap, rest)


def ledger():
    return config.state() / "refusals.jsonl"


def level(cfg):
    return config.level(cfg, KEY, DEFAULT)


# ---- what a refusal is


def kind(text):
    """("refused", category or "human"), ("uncategorized", None) or (None, None) for a tool result's text."""
    if not isinstance(text, str):
        return None, None
    if m := CATEGORY.match(text):
        return "refused", m.group(1)
    if HUMAN.match(text):
        return "refused", "human"
    if CLASSIFIER.match(text) or NO_VERDICT.match(text):
        return "uncategorized", None
    return None, None


def reason_kind(reason):
    """The same for a PermissionDenied event's `reason` (the classifier's alone; the human's never comes there)."""
    m = REASON_CATEGORY.match(reason) if isinstance(reason, str) else None
    return ("refused", m.group(1)) if m else ("uncategorized", None)


# ---- what an act is


def git(args, cwd):
    """stdout of a git command; Unknown when it fails, since every caller needs the answer. Running out of time is a
    TimeoutError, which the hook reports."""
    p = command(["git", *args], cwd, 3)
    if p is None:
        raise Unknown(f"git {args[0]}")
    if p.returncode or not p.stdout.strip():
        raise Unknown(f"git {args[0]}")
    return p.stdout.strip()


def command(argv, cwd, cap):
    """The finished process, None when it could not start; TimeoutError when it outran the hook's time."""
    timeout = left(cap)
    try:
        return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise TimeoutError(f"{argv[0]} {argv[1]} took over {timeout:.1f} s") from e
    except OSError:
        return None


def repo_key(url):
    """`host/owner/repo` of a remote URL (https, ssh or scp form), None when it is not one."""
    m = REMOTE.match(url or "")
    return f"{m.group(1).lower()}/{m.group(2)}/{m.group(3)}" if m else None


def remote_repo(remote, cwd, push=False):
    """`host/owner/repo` of a remote name or URL; with push, the URL a push goes to (one only)."""
    if "/" in remote or ":" in remote:
        url = remote
    else:
        url = git(["remote", "get-url", *(["--push", "--all"] if push else []), remote], cwd)
        if "\n" in url:
            raise Unknown("remote with several push URLs")
    key = repo_key(url)
    if not key:
        raise Unknown("remote")
    return key


def gh_repo(value, cwd):
    """`host/owner/repo` for gh's -R value, else the checkout's origin."""
    if value:
        parts = value.split("/")
        host = os.environ.get("GH_HOST") or "github.com"
        key = f"{host}/{value}" if len(parts) == 2 else value if len(parts) == 3 else repo_key(value)
        if not key:
            raise Unknown("gh -R")
        return key
    return remote_repo("origin", known(cwd))


def opt_value(words, names):
    for i, x in enumerate(words):
        if x in names and i + 1 < len(words):
            return words[i + 1]
        for n in names:
            if n.startswith("--") and x.startswith(n + "="):
                return x.split("=", 1)[1]
    return None


def operands(words, valued=()):
    """The words that are not options (or an option's value)."""
    out, i = [], 0
    while i < len(words):
        x = words[i]
        if x == "--":
            return out + words[i + 1:]
        if x.startswith("-") and x != "-":
            i += 2 if x in valued else 1
            continue
        out.append(x)
        i += 1
    return out


def known(here):
    """The directory, or Unknown after a `cd` this reader could not follow (or with no cwd at all)."""
    if here is danger.UNKNOWN_DIR or not here:
        raise Unknown("directory")
    return here


def full_path(word, here):
    """The absolute path a word names: a leading `~` and $HOME expanded, a quoted `$` or `~` kept as written. Unknown
    when bash would expand any other part (a variable, a glob)."""
    if "~" in word[1:]:
        raise Unknown("expanded path")
    home = os.path.expanduser("~")
    word = HOME_VAR.sub(lambda _: home, word)  # the value itself: `$HOME.old` is a sibling of home, not inside it
    if "$" in word or set(word) & set("*?["):
        raise Unknown("expanded path")
    word = os.path.expanduser(word).replace(shell.TEXT_DOLLAR, "$").replace(shell.TEXT_TILDE, "~")
    return word if os.path.isabs(word) else os.path.join(known(here), word)


def directory(word, here):
    """The real directory a `git -C` word names (git changes directory physically); Unknown when it cannot be read."""
    return os.path.realpath(full_path(word, here))


def changed_dir(c, here):
    """The directory a `cd` leaves the shell in, UNKNOWN_DIR when it cannot be read from its words. Logical, as bash's
    `cd` is without -P: `cd link; cd ..` returns to the link's own parent."""
    opts = [x for x in c.words[1:] if x.startswith("-") and x != "-"]
    args = [x for x in c.words[1:] if not x.startswith("-") or x == "-"]
    if not args:
        return os.path.expanduser("~")
    if args[0] == "-":
        return danger.UNKNOWN_DIR
    try:
        p = full_path(args[0], here)
    except Unknown:
        return danger.UNKNOWN_DIR
    return os.path.realpath(p) if any("P" in x for x in opts) else os.path.normpath(p)


def deleted(word, here):
    """A deleted path's key: its real parent joined with its own name, so deleting a symlink is not deleting its
    target."""
    # No normpath first: `link/../x` goes through the link, so `..` is resolved by realpath on the real parent.
    p = full_path(word, here).rstrip("/") or "/"
    name = os.path.basename(p)
    if name in ("", ".", ".."):
        return os.path.realpath(p)
    return os.path.join(os.path.realpath(os.path.dirname(p)), name)


def each_deleted(words, here, unknown):
    """[("delete", key)] for each path word; a word that cannot be read goes to `unknown` and the rest still count."""
    out = []
    for x in words:
        try:
            out.append(("delete", deleted(x, here)))
        except Unknown as e:
            unknown.append(f"{e}")
    return out


def pr_target(arg, repo, cwd):
    """`host/owner/repo#n` for gh's PR argument (number, URL, or a branch or nothing that gh resolves). `repo` is
    called only when the argument is not a URL."""
    if arg and (m := PR_URL.match(arg)):
        return f"{m.group(1).lower()}/{m.group(2)}/{m.group(3)}#{m.group(4)}"
    repo = repo()
    if arg and arg.lstrip("#").isdigit():
        return f"{repo}#{arg.lstrip('#')}"
    p = command(["gh", "pr", "view", *([arg] if arg else []), "-R", repo, "--json", "url", "-q", ".url"],
                cwd if cwd is not danger.UNKNOWN_DIR else None, 4)
    if p and p.returncode == 0 and (m := PR_URL.match(p.stdout.strip())):
        return f"{m.group(1).lower()}/{m.group(2)}/{m.group(3)}#{m.group(4)}"
    raise Unknown("gh pr view")


def git_dir(w, here):
    """(subcommand, its words, the directory git runs in) with each `-C` applied in turn."""
    i = 1
    while i < len(w):
        x = w[i]
        if x == "-C" and i + 1 < len(w):
            here = directory(w[i + 1], here)
            i += 2
        elif x in danger.GIT_OPTS:
            i += 2
        elif x.startswith("-"):
            i += 1
        else:
            return x, w[i + 1:], here
    return None, [], here


def removed_worktrees(words, here, unknown):
    """[("delete", key)] for each `git worktree remove` operand, as git picks it from its own worktree list: the path,
    or a trailing part of one worktree's path (git accepts `victim` for `/elsewhere/victim`)."""
    listed = [line[9:] for line in git(["worktree", "list", "--porcelain"], known(here)).splitlines()
              if line.startswith("worktree ")]
    out = []
    for x in words:
        try:
            full = os.path.realpath(full_path(x, here))
        except Unknown as e:
            unknown.append(str(e))
            continue
        hits = [p for p in listed if os.path.realpath(p) == full]
        hits = hits or [p for p in listed if ("/" + p.rstrip("/")).endswith("/" + x.strip("/"))]
        if len(hits) == 1:
            out.append(("delete", deleted(hits[0], here)))
        else:
            unknown.append("worktree not in git's list")
    return out


def git_acts(w, here, unknown):
    sub, rest, here = git_dir(w, here)
    if sub == "worktree" and rest[:1] in (["remove"], ["rm"]):
        return removed_worktrees(operands(rest[1:]), here, unknown)
    if sub == "reset" and "--hard" in rest:
        return [("reset-hard", os.path.realpath(git(["rev-parse", "--show-toplevel"], known(here))))]
    if sub != "push" or any(x in ("-n", "--dry-run") for x in rest):
        return []
    if any(x in ("--all", "--mirror", "--tags", "--branches") or x.startswith("--prune") for x in rest):
        raise Unknown("git push of many refs")
    args = operands(rest, PUSH_VALUED)
    cwd = known(here)
    repo_arg = opt_value(rest, {"--repo"})
    if repo_arg is None and args:
        repo_arg, args = args[0], args[1:]
    branch = None
    if not args:
        branch = git(["symbolic-ref", "--short", "HEAD"], cwd)  # a detached HEAD has none: Unknown
        # With no refspec git pushes by push.default; only the current branch's own name is certain.
        if git_config("push.default", cwd) not in (None, "simple", "current"):
            raise Unknown("git push with push.default")
    if repo_arg is None:
        repo_arg = git_config(f"branch.{branch}.pushRemote", cwd) or git_config("remote.pushDefault", cwd) or \
            git_config(f"branch.{branch}.remote", cwd) or "origin"
    # A remote's own push refspecs win over push.default when none is given.
    if not args and "/" not in repo_arg and ":" not in repo_arg and git_config(f"remote.{repo_arg}.push", cwd):
        raise Unknown("git push with remote push refspecs")
    repo = remote_repo(repo_arg, cwd, push=True)
    force = any(x in ("-f", "--force", "--force-if-includes") or x.startswith("--force-with-lease") or
                re.fullmatch(r"-[a-zA-Z]*f[a-zA-Z]*", x) for x in rest)
    delete = any(x in ("-d", "--delete") for x in rest)
    out = []
    for spec in args or [branch]:
        plus = spec.startswith("+")
        src, colon, dst = spec.lstrip("+").partition(":")
        dst = (dst if colon else src).removeprefix("refs/heads/")
        if dst == "HEAD":
            dst = git(["symbolic-ref", "--short", "HEAD"], cwd)
        if not dst or dst.startswith("refs/") or "*" in dst:
            raise Unknown("refspec")
        v = "delete-branch" if delete or (colon and not src) else "force-push" if force or plus else "push"
        out.append((v, f"{repo}:{dst}"))
    return out


def git_config(name, cwd):
    try:
        return git(["config", "--get", name], cwd)
    except Unknown:
        return None


def gh_acts(w, here):
    if w[1:2] == ["pr"] and len(w) > 2 and w[2] in ("merge", "close"):
        repo = lambda: gh_repo(opt_value(w[3:], GH_REPO), here)
        args = operands(w[3:], GH_REPO | {"-b", "--body", "-F", "--body-file", "-t", "--subject", "-A", "--author-email",
                                          "--match-head-commit", "-c", "--comment"})
        return [(w[2], pr_target(args[0] if args else None, repo, here))]
    if w[1:2] == ["api"]:
        m = next((m for x in w[2:] if (m := API_MERGE.match(x))), None)
        if not m:
            return []
        # Attached forms count too: `-XPUT`, `-fkey=v`, `--input=f`.
        fields = any(x in ("-f", "-F", "--field", "--raw-field", "--input") or
                     x.startswith(("--field=", "--raw-field=", "--input=")) or re.fullmatch(r"-[fF].+", x)
                     for x in w[2:])
        attached = next((x[2:] for x in w[2:] if re.fullmatch(r"-X.+", x)), None)
        method = (opt_value(w[2:], {"-X", "--method"}) or attached or ("POST" if fields else "GET")).upper()
        if method == "GET":
            return []
        host = opt_value(w, {"--hostname"}) or os.environ.get("GH_HOST") or "github.com"
        return [("merge", f"{host}/{m.group(1)}/{m.group(2)}#{m.group(3)}")]
    return []


def bash_acts(cmd, cwd):
    """([(verb, target)], [what could not be read])."""
    out, unknown, here = [], [], cwd or danger.UNKNOWN_DIR
    # A `cd` inside a subshell or substitution changes only that scope, which this reader does not follow.
    masked = shell.command_text(cmd)
    scoped = "(" in masked or "`" in masked
    for c in shell.commands(cmd):
        w = c.words
        if c.name in ("cd", "pushd", "popd"):
            here = danger.UNKNOWN_DIR if scoped or c.name != "cd" else changed_dir(c, here)
            continue
        if not w:
            continue
        try:
            if c.name == "rm":
                out += each_deleted(operands(w[1:]), here, unknown)
            elif c.name == "git":
                out += git_acts(w, here, unknown)
            elif c.name == "gh":
                out += gh_acts(w, here)
            elif c.name == "orca" and w[1:3] in (["worktree", "rm"], ["worktree", "remove"]):
                sel = opt_value(w[3:], {"--worktree"}) or ""
                if sel == "current":
                    out.append(("delete", os.path.realpath(git(["rev-parse", "--show-toplevel"], known(here)))))
                elif sel.startswith("path:"):
                    out.append(("delete", deleted(sel[5:], here)))
                else:
                    raise Unknown("orca worktree selector")
        except Unknown as e:
            unknown.append(f"{c.name}: {e}")
    return list(dict.fromkeys(out)), unknown


def acts(tool, args, cwd):
    """([(verb, target)] the call would change, [parts that change something but could not be read])."""
    args = args if isinstance(args, dict) else {}
    if tool == "Bash":
        if not isinstance(args.get("command"), str):
            return [], [f"{MISSING} Bash command"]
        return bash_acts(args["command"], cwd)
    if tool in WRITE_TOOLS:
        p = args.get("file_path") or args.get("notebook_path")
        if not isinstance(p, str) or not p:
            return [], [f"{MISSING} {tool} path"]
        p = os.path.expanduser(p)
        if not os.path.isabs(p) and not cwd:
            return [], [f"{MISSING} cwd for a relative {tool} path"]
        return [("write", os.path.realpath(os.path.join(cwd or "/", p)))], []
    if tool in READ_ONLY:
        return [], []
    return [], [str(tool)]


def matches(refused, act):
    rv, rt = refused
    v, t = act
    if v != rv:
        return False
    # Deleting a directory deletes what a refused deletion inside it named.
    return t == rt or (v == "delete" and t.startswith("/") and rt.startswith(t.rstrip("/") + "/"))


# ---- the ledger


@contextlib.contextmanager
def locked():
    config.state().mkdir(parents=True, exist_ok=True)
    with open(config.state() / "refusals.lock", "a") as f:
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                left(10)
                time.sleep(0.02)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def rows():
    try:
        text = ledger().read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and isinstance(r.get("id"), str):
            out.append(r)
    return out


def seen(rs=None):
    """Event ids the ledger already handled (recorded, skipped, cleared or done)."""
    return {r["id"] for r in (rows() if rs is None else rs)}


def started(rs=None):
    """When the ledger began (its `start` row), None before it exists."""
    return next((r.get("at") for r in (rows() if rs is None else rs) if r.get("op") == "start"), None)


def open_refusals(rs=None):
    rs = rows() if rs is None else rs
    closed = {r["id"] for r in rs if r.get("op") in ("clear", "done")}
    return [r for r in rs if r.get("op") == "refuse" and r["id"] not in closed]


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def start():
    """Writes the `start` row when the ledger does not exist yet; returns when the ledger began."""
    with locked():
        if at := started():
            return at
        at = now()
        with open(ledger(), "a", encoding="utf-8") as f:
            f.write(json.dumps({"op": "start", "id": "start", "at": at}) + "\n")
        return at


def record(event_id, found, session=None, terminal=None, run=None):
    """Appends one refuse row per (verb, target), or one skip row when found is empty; nothing when the event id is
    already in the ledger. Returns the rows written."""
    start()
    with locked():
        if event_id in seen():
            return []
        base = {"at": now(), "id": event_id}
        new = ([{"op": "refuse", **base, "session": session, "terminal": terminal, "run": run, "verb": v, "target": t}
                for v, t in found] or [{"op": "skip", **base}])
        with open(ledger(), "a", encoding="utf-8") as f:
            f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in new))
        return new


def blocked(found, rs=None):
    """The open refusals any of found would do again."""
    return [r for r in open_refusals(rs) if any(matches((r.get("verb"), r.get("target") or ""), a) for a in found)]


def clear(event_id, tty):
    """Clears an open refusal (the human's `orch refusal clear`); records whether a terminal typed it. A refusal
    cleared over KEEP_DAYS ago shrinks to one `done` row with its id. Returns the cleared refuse rows."""
    with locked():
        rs = rows()
        hit = [r for r in open_refusals(rs) if r["id"] == event_id]
        if hit:
            rs.append({"op": "clear", "at": now(), "id": event_id, "tty": bool(tty)})
        cut = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        old = {r["id"]: r["at"] for r in rs if r.get("op") == "clear" and str(r.get("at")) < cut}
        keep = [r for r in rs if r["id"] not in old] + [{"op": "done", "id": i, "at": at} for i, at in old.items()]
        tmp = ledger().with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep), encoding="utf-8")
        os.replace(tmp, ledger())
        return hit


def run_id():
    """This terminal's Orca Run id, None outside Orca or when Orca does not answer in time."""
    if not os.environ.get("ORCA_TERMINAL_HANDLE"):
        return None
    try:
        p = subprocess.run(["orca", "orchestration", "run-current", "--json"], capture_output=True, text=True,
                           timeout=left(2))
        run = (json.loads(p.stdout).get("result") or {}).get("run") if p.returncode == 0 else None
        return run.get("id") if isinstance(run, dict) else None
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError, TimeoutError):
        return None


# ---- the transcript (refusals no PermissionDenied event reported)


def tail(path):
    """Transcript entries of the last TAIL_BYTES."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - TAIL_BYTES))
        lines = f.read().splitlines()
    out = []
    for raw in lines:
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        if isinstance(e, dict):
            out.append(e)
    return out


def result_text(block):
    c = block.get("content")
    if isinstance(c, str):
        return c
    return " ".join(x.get("text") or "" for x in c or [] if isinstance(x, dict))


def stamp(ts):
    """A transcript timestamp in the ledger's form, None when there is none."""
    try:
        return dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def denials(entries, since=None):
    """[(tool_use_id, tool name, tool input, cwd, session id, kind, category)] for each refused call in entries, those
    stamped before `since` left out."""
    uses, out = {}, []
    for e in entries:
        content = (e.get("message") or {}).get("content")
        for b in content if isinstance(content, list) else []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and isinstance(b.get("id"), str):
                uses[b["id"]] = (b.get("name"), b.get("input"))
            elif b.get("type") == "tool_result" and b.get("is_error") and isinstance(b.get("tool_use_id"), str):
                at = stamp(e.get("timestamp")) if e.get("timestamp") else None
                if since and at and at < since:
                    continue
                k, cat = kind(result_text(b))
                if k:
                    name, args = uses.get(b["tool_use_id"], (None, None))
                    out.append((b["tool_use_id"], name, args, e.get("cwd"), e.get("sessionId"), k, cat))
    return out
