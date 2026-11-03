"""The refused-act ledger: an act the auto mode classifier (with a category) or the human refused is not done again by
any session until the human clears it.

What counts as a refusal is read from the tool result text Claude Code writes: the classifier's
"Permission for this action was denied by the Claude Code auto mode classifier. Reason: [<category>]" and the human's
"The user doesn't want to proceed with this tool use." / "Permission for this tool use was denied. The tool use was
rejected". A hook's block, a `<tool_use_error>`, and a classifier refusal with no category (no verdict, or judged
dangerous with no explanation) are not recorded: each has its own documented way forward.

An act is a normalized (verb, target): a path by its real path, a PR as `host/owner/repo#n`, a branch as
`host/owner/repo:branch`. Deleting one path the same way counts whichever command does it (`rm -rf`,
`git worktree remove`, `orca worktree rm`), and deleting a directory that holds a refused deletion's target matches
it too. Only the shapes in `acts` are recognized; any other refused call is reported as not recorded.

$AGENT_SKILLS_STATE/refusals.jsonl holds per line the time, the event (tool use) id, the session, the Orca terminal,
the Run, the verb and the target. Never the command text or other argument values: they can hold a secret.
Every write takes an fcntl lock on refusals.lock. Only `orch refusal clear <id>`, typed by the human as a `!` line,
clears a refusal (guard.deny.owner_only refuses it from an agent); a cleared refusal leaves the file 30 days later.
Scripts that run commands themselves call `blocked(acts(...))` before they run them.
"""
import contextlib, datetime as dt, fcntl, json, os, pathlib, re, subprocess

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
PR_URL = re.compile(r"https?://([^/\s]+)/([\w.-]+)/([\w.-]+)/pull/(\d+)")
REMOTE = re.compile(r"(?:^[\w+.-]+://(?:[^@/]+@)?|^[^@/:]+@|^)([^/:]+)[:/]+([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
API_MERGE = re.compile(r"^/?repos/([\w.-]+)/([\w.-]+)/pulls/(\d+)/merge$")


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


def git(args, cwd, timeout=3):
    """stdout of a git command, "" when it fails."""
    try:
        p = subprocess.run(["git", *args], cwd=cwd or None, capture_output=True, text=True, timeout=timeout)
        return p.stdout.strip() if p.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def repo_key(url):
    """`host/owner/repo` of a remote URL (https, ssh or scp form), None when it is not one."""
    m = REMOTE.match(url or "")
    return f"{m.group(1).lower()}/{m.group(2)}/{m.group(3)}" if m else None


def remote_repo(remote, cwd):
    return repo_key(remote) if "/" in remote or ":" in remote else repo_key(git(["remote", "get-url", remote], cwd))


def gh_repo(value, cwd):
    """`host/owner/repo` for gh's -R value, else the checkout's origin."""
    if value:
        parts = value.split("/")
        host = os.environ.get("GH_HOST") or "github.com"
        return f"{host}/{value}" if len(parts) == 2 else value if len(parts) == 3 else repo_key(value)
    return remote_repo("origin", cwd)


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


def pr_target(arg, repo, cwd):
    """`host/owner/repo#n` for gh's PR argument (number, URL, branch or none); a branch gh cannot turn into a number
    stays `host/owner/repo:branch`."""
    if arg and (m := PR_URL.match(arg)):
        return f"{m.group(1).lower()}/{m.group(2)}/{m.group(3)}#{m.group(4)}"
    if arg and arg.lstrip("#").isdigit():
        return f"{repo}#{arg.lstrip('#')}" if repo else None
    try:
        p = subprocess.run(["gh", "pr", "view", *([arg] if arg else []), *(["-R", repo] if repo else []), "--json", "url",
                            "-q", ".url"], cwd=cwd or None, capture_output=True, text=True, timeout=4)
        if p.returncode == 0 and (m := PR_URL.match(p.stdout.strip())):
            return f"{m.group(1).lower()}/{m.group(2)}/{m.group(3)}#{m.group(4)}"
    except (OSError, subprocess.SubprocessError):
        pass
    branch = arg or git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    return f"{repo}:{branch}" if repo and branch else None


def path_target(word, here):
    p = danger.expand(word, here)
    return p.rstrip("/") or "/" if p else None


def worktree_selector(sel, here):
    """The real path of an `orca worktree rm --worktree` selector; `orca:<selector>` for an id Orca alone resolves."""
    if sel == "current":
        top = git(["rev-parse", "--show-toplevel"], here if here is not danger.UNKNOWN_DIR else None)
        return os.path.realpath(top) if top else None
    if sel.startswith("path:"):
        return path_target(sel[5:], here)
    return f"orca:{sel}"


def git_acts(w, here):
    sub, rest = danger.verb(w[1:], danger.GIT_OPTS)
    if (d := opt_value(w[1:w.index(sub)] if sub in w else [], {"-C"})) is not None:
        here = danger.expand(d, here) or danger.UNKNOWN_DIR
    cwd = None if here is danger.UNKNOWN_DIR else here
    if sub == "worktree" and rest[:1] in (["remove"], ["rm"]):
        return [("delete", t) for x in operands(rest[1:]) if (t := path_target(x, here))]
    if sub == "reset" and "--hard" in rest:
        top = git(["rev-parse", "--show-toplevel"], cwd)
        return [("reset-hard", os.path.realpath(top))] if top else []
    if sub != "push":
        return []
    args = operands(rest, {"-o", "--push-option", "--repo", "--receive-pack", "--exec"})
    force = any(x in ("-f", "--force", "--force-if-includes") or x.startswith("--force-with-lease") or
                re.fullmatch(r"-[a-zA-Z]*f[a-zA-Z]*", x) for x in rest)
    delete = any(x in ("-d", "--delete") for x in rest)
    remote = args[0] if args else (git(["config", f"branch.{git(['rev-parse', '--abbrev-ref', 'HEAD'], cwd)}.remote"], cwd)
                                   or "origin")
    repo = remote_repo(remote, cwd)
    refspecs = args[1:] or [git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)]
    out = []
    for spec in refspecs:
        if not spec or not repo:
            continue
        plus = spec.startswith("+")
        src, _, dst = spec.lstrip("+").partition(":")
        dst = (dst if _ else src).removeprefix("refs/heads/")
        if not dst or dst == "HEAD":
            dst = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
        v = "delete-branch" if delete or (_ and not src) else "force-push" if force or plus else "push"
        out.append((v, f"{repo}:{dst}"))
    return out


def gh_acts(w, here):
    cwd = None if here is danger.UNKNOWN_DIR else here
    if w[1:2] == ["pr"] and len(w) > 2 and w[2] in ("merge", "close"):
        repo = gh_repo(opt_value(w[3:], GH_REPO), cwd)
        args = operands(w[3:], GH_REPO | {"-b", "--body", "-F", "--body-file", "-t", "--subject", "-A", "--author-email",
                                          "--match-head-commit", "-c", "--comment"})
        t = pr_target(args[0] if args else None, repo, cwd)
        return [(w[2], t)] if t else []
    if w[1:2] == ["api"]:
        for x in w[2:]:
            if m := API_MERGE.match(x):
                host = opt_value(w, {"--hostname"}) or os.environ.get("GH_HOST") or "github.com"
                return [("merge", f"{host}/{m.group(1)}/{m.group(2)}#{m.group(3)}")]
    return []


def bash_acts(cmd, cwd):
    out, here = [], cwd
    for c in shell.commands(cmd):
        w = c.words
        if c.name == "cd":
            here = danger.after_cd(c, here)
            continue
        if not w:
            continue
        if c.name == "rm":
            out += [("delete", t) for x in operands(w[1:]) if (t := path_target(x, here))]
        elif c.name == "git":
            out += git_acts(w, here)
        elif c.name == "gh":
            out += gh_acts(w, here)
        elif c.name == "orca" and w[1:3] in (["worktree", "rm"], ["worktree", "remove"]):
            sel = opt_value(w[3:], {"--worktree"})
            if sel and (t := worktree_selector(sel, here)):
                out.append(("delete", t))
    return list(dict.fromkeys(out))


def acts(tool, args, cwd):
    """[(verb, target)] the call would change; [] for a read-only call or a shape this module does not know."""
    args = args if isinstance(args, dict) else {}
    if tool == "Bash" and isinstance(args.get("command"), str):
        return bash_acts(args["command"], cwd)
    if tool in WRITE_TOOLS:
        p = args.get("file_path") or args.get("notebook_path")
        return [("write", os.path.realpath(os.path.join(cwd or ".", os.path.expanduser(p))))] if isinstance(p, str) and p else []
    return []


def changes(tool):
    """The tool can change something: a refused call of it that `acts` cannot read is worth a warning."""
    return tool not in READ_ONLY


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
        fcntl.flock(f, fcntl.LOCK_EX)
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
    """Event ids the ledger already handled (recorded, skipped or cleared)."""
    return {r["id"] for r in (rows() if rs is None else rs)}


def open_refusals(rs=None):
    rs = rows() if rs is None else rs
    cleared = {r["id"] for r in rs if r.get("op") == "clear"}
    return [r for r in rs if r.get("op") == "refuse" and r["id"] not in cleared]


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record(event_id, found, session=None, terminal=None, run=None):
    """Appends one refuse row per (verb, target), or one skip row when found is empty; nothing when the event id is
    already in the ledger. Returns the rows written."""
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
    """Clears an open refusal (the human's `orch refusal clear`); records whether a terminal typed it. Drops what was
    cleared or skipped over KEEP_DAYS ago. Returns the cleared refuse rows ([] when none was open)."""
    with locked():
        rs = rows()
        hit = [r for r in open_refusals(rs) if r["id"] == event_id]
        if hit:
            rs.append({"op": "clear", "at": now(), "id": event_id, "tty": bool(tty)})
        cut = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        old = {r["id"] for r in rs if r.get("op") in ("clear", "skip") and str(r.get("at")) < cut}
        keep = [r for r in rs if r["id"] not in old]
        tmp = ledger().with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep), encoding="utf-8")
        os.replace(tmp, ledger())
        return hit


def run_id():
    """This terminal's Orca Run id, None outside Orca or when Orca does not answer quickly."""
    if not os.environ.get("ORCA_TERMINAL_HANDLE"):
        return None
    try:
        p = subprocess.run(["orca", "orchestration", "run-current", "--json"], capture_output=True, text=True, timeout=2)
        run = (json.loads(p.stdout).get("result") or {}).get("run") if p.returncode == 0 else None
        return run.get("id") if isinstance(run, dict) else None
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
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


def denials(entries):
    """[(tool_use_id, tool name, tool input, cwd, session id, kind, category)] for each refused call in entries."""
    uses, out = {}, []
    for e in entries:
        content = (e.get("message") or {}).get("content")
        for b in content if isinstance(content, list) else []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and isinstance(b.get("id"), str):
                uses[b["id"]] = (b.get("name"), b.get("input"))
            elif b.get("type") == "tool_result" and b.get("is_error") and isinstance(b.get("tool_use_id"), str):
                k, cat = kind(result_text(b))
                if k:
                    name, args = uses.get(b["tool_use_id"], (None, None))
                    out.append((b["tool_use_id"], name, args, e.get("cwd"), e.get("sessionId"), k, cat))
    return out
