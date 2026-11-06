"""Work-discipline checks for a card's tool calls (design M4), called by guard.py for Bash, Monitor, Edit, Write,
MultiEdit and NotebookEdit. Six keys under `worker.` in ~/.claude/agent-skills.json, each `warn` unless set:

  worker.edit_tool      Bash that writes a file: `>`, `>>`, `&>`, `tee`, `sed -i`, `python -c` or a python heredoc
                        that opens a file for writing. Temp directories, /dev/null and orch-refusal are not writes.
  worker.chain          a mutating command (rm, mv, git commit, git push, worker_done) in one call with another command.
  worker.paths          Edit/Write outside the card's worktree, the temp directories and orch-refusal, or inside the
                        plugin cache.
  worker.main_checkout  Edit/Write in a repository's main checkout (`--git-dir` equals `--git-common-dir`).
  worker.memory         a write to ~/.claude/projects/*/memory/ (edit tools, and the Bash targets edit_tool reads).
  worker.pre_push       `git rebase` of a branch that was pushed; `git push` or a code-only `git commit` in a session
                        that has run no test command.

Who is reached is roles.py `worker_discipline`: a worker or CLOSE/KEEP card, and a subagent it started; never a human's
session, an orchestrator or a lead. `deny` and `ask` act through guard.py like any finding; every firing, whatever its
level, is also a row in $AGENT_SKILLS_STATE/turn-warnings.jsonl (config.append_warning), so a week of rows shows each
key's false-positive rate before it is raised. Text heuristics: a script file's own commands, `bash -c` and an `eval`
are not read. A fact that cannot be read (no git, no transcript) never fires.
"""
import datetime, json, os, pathlib, re, subprocess

import roles
from lib import config, shell
from lib.danger import GIT_OPTS, PYTHON, Finding

KEYS = ("worker.edit_tool", "worker.chain", "worker.paths", "worker.main_checkout", "worker.memory", "worker.pre_push")
SOURCE = "(agent-skills hooks/guard.py, {key})"
TEMP_ROOTS = ("/tmp", "/private/tmp", "/var/folders", "/private/var/folders")
# Pseudo-files and the temp variable: a write there leaves nothing in a repository.
NULLS = ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/fd/")
TEMP_VARS = ("$TMPDIR", "${TMPDIR}")
WRITE_OPS = {">", ">>", ">|", "&>", "&>>"}
PYTHON_WRITE = re.compile(
    r"""\.write_(?:text|bytes)\(|\bopen\([^)]*,\s*(?:mode\s*=\s*)?['"][^'"]*[wax+][^'"]*['"]""")
PYTHON_HEREDOC = re.compile(
    r"""(?:^|[;&|(\s])python[\d.]*[ \t]+(?:-[ \t]*)?<<-?[ \t]*(['"]?)(\w+)\1[ \t]*\n(.*?)\n[ \t]*\2(?:[ \t]*(?:$|[;&|)\n]))""",
    re.S | re.M)
SED_INPLACE = re.compile(r"-[A-Za-z]*i|--in-place(?:=.*)?$")
MEMORY = re.compile(r"/\.claude/projects/[^/]+/memory(?:/|$)")
PLUGIN_CACHE = re.compile(r"/\.claude/plugins/cache(?:/|$)")
MUTATING_GIT = {"commit", "push"}
TEST_CMD = re.compile(
    r"\b(?:pytest|unittest|vitest|jest|go test|cargo test|make test|(?:npm|yarn|pnpm)(?: run)? test|orch heavy)\b"
    r"|\btest_\w+\.py\b|\bci_tests\.py\b")
CODE_FILE = re.compile(r"\.(?:py|ts|tsx|js|jsx|mjs|go|rs|rb|java|kt|swift|sh|c|cc|cpp|h)$")
TEST_FILE = re.compile(r"(?:^|/)(?:test_[^/]*|[^/]*_test\.[^/]+|[^/]*\.test\.[^/]+|[^/]*\.spec\.[^/]+|tests?/[^/]+)$")
TRANSCRIPT_MAX = 64 << 20
GIT_TIMEOUT = 3


def level(cfg, key):
    return config.level(cfg, key, "warn")


def fire(cfg, event, key, reason):
    """[Finding] for a key that is on; the row in turn-warnings.jsonl is written here."""
    lvl = level(cfg, key)
    if lvl == "off":
        return []
    config.append_warning({
        "at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "event": "PreToolUse", "session": event.get("session_id"), "agent": event.get("agent_id") or "",
        "check": key, "level": lvl})
    return [Finding(key, lvl, f"{reason} {SOURCE.format(key=key)}")]


def temp_roots():
    roots = set(TEMP_ROOTS)
    if tmpdir := os.environ.get("TMPDIR"):
        roots.add(tmpdir.rstrip("/"))
    return roots | {os.path.realpath(r) for r in roots}


def resolve(path, cwd):
    p = pathlib.Path(path).expanduser()
    return os.path.realpath(p if p.is_absolute() else os.path.join(cwd or ".", p))


def under(path, roots):
    return any(path == r or path.startswith(r.rstrip("/") + "/") for r in roots)


def scratch(path, cwd):
    """The path is a temp directory, the orch-refusal directory or a pseudo-file: not a place a card's work lives."""
    if not isinstance(path, str) or not path:
        return False
    if path.startswith(NULLS) or path.startswith(TEMP_VARS):
        return True
    full = resolve(path, cwd)
    return under(full, temp_roots()) or "orch-refusal" in pathlib.PurePath(full).parts


def git(cwd, *args):
    """stdout of `git -C cwd args`, None when git fails or cannot run."""
    try:
        p = subprocess.run(["git", "-C", cwd or ".", *args], capture_output=True, text=True, timeout=GIT_TIMEOUT,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


# ---- Bash ---------------------------------------------------------------------------------------------------------

def write_targets(cmds, cwd):
    """[(how, target)] of the file writes the commands make, scratch targets left out. target is None when the
    command writes a file this reader cannot name (a python snippet)."""
    out = []
    for c in cmds:
        for fd, op, target in c.redirects:
            if op == ">&" and (target.isdigit() or target == "-"):
                continue
            if (op in WRITE_OPS or op == ">&") and not scratch(target, cwd):
                out.append((f"`{fd}{op}` redirect", target))
        w = c.words
        if c.name == "tee":
            out += [("`tee`", f) for f in w[1:] if not f.startswith("-") and not scratch(f, cwd)]
        elif c.name == "sed" and any(SED_INPLACE.match(x) for x in w[1:]) and len(w) > 2 and not scratch(w[-1], cwd):
            out.append(("`sed -i`", w[-1]))
        elif PYTHON.fullmatch(c.name) and "-c" in w[1:-1]:
            code = w[w.index("-c") + 1]
            if PYTHON_WRITE.search(code) and "tempfile" not in code and not any(r in code for r in TEMP_ROOTS):
                out.append(("`python -c` file write", None))
    return out


def python_heredocs(cmd):
    """[(how, None)] for each python heredoc body that opens a file for writing."""
    return [("python heredoc file write", None) for m in PYTHON_HEREDOC.finditer(cmd)
            if PYTHON_WRITE.search(m.group(3)) and "tempfile" not in m.group(3)
            and not any(r in m.group(3) for r in TEMP_ROOTS)]


def top_units(cmds):
    """The commands a call runs one after another: those not inside another command's span (a `$(…)` body is part
    of its outer command), with a pipeline counted once."""
    tops = [c for c in cmds if not any(o is not c and o.span[0] <= c.span[0] and c.span[1] <= o.span[1]
                                       and o.span != c.span for o in cmds)]
    return [c for c in tops if not c.piped_from]


def git_verb(words):
    """The git subcommand of `git [opts] verb …`, None for another command."""
    if not words or os.path.basename(words[0]) != "git":
        return None
    i = 1
    while i < len(words):
        if words[i] in GIT_OPTS:
            i += 2
        elif words[i].startswith("-"):
            i += 1
        else:
            return words[i]
    return None


def mutating(c):
    w = c.words
    if c.name in ("rm", "mv"):
        return c.name
    if (verb := git_verb(w)) in MUTATING_GIT:
        return f"git {verb}"
    if c.name == "orca" and "worker_done" in w:
        return "worker_done"
    return None


def session_ran(transcript, pattern):
    """True when a Bash call earlier in the session matches pattern (a regex), False when none does, None when the
    transcript cannot be read."""
    try:
        if not transcript or os.path.getsize(transcript) > TRANSCRIPT_MAX:
            return None
        with open(transcript, encoding="utf-8", errors="replace") as f:
            for raw in f:
                if '"Bash"' not in raw:
                    continue
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                content = (entry.get("message") or {}).get("content") if isinstance(entry, dict) else None
                for b in content if isinstance(content, list) else ():
                    if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Bash" \
                            and pattern.search(str((b.get("input") or {}).get("command") or "")):
                        return True
    except OSError:
        return None
    return False


GIT_PUSH = re.compile(r"\bgit\b[^\n;&|]*\bpush\b")


def pre_push(cmds, event):
    """The reason `git rebase|push|commit` is early, None when it is not."""
    cwd, transcript = event.get("cwd"), event.get("transcript_path")
    for c in cmds:
        verb = git_verb(c.words)
        if verb == "rebase" and not {"--abort", "--continue", "--skip", "--quit"} & set(c.words):
            branch = (git(cwd, "branch", "--show-current") or "").strip()
            if branch and git(cwd, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}") is not None:
                return (f"`git rebase` of `{branch}`, which origin already has: a pushed branch takes a change on "
                        "main by merging main in, because a rebase needs a force-push.")
        elif verb == "push":
            if session_ran(transcript, TEST_CMD) is False and session_ran(transcript, GIT_PUSH) is False:
                return "The first `git push` of this session and no test command has run in it: run the tests first."
        elif verb == "commit":
            names = (git(cwd, "diff", "--cached", "--name-only") or "").split()
            code = [n for n in names if CODE_FILE.search(n) and not TEST_FILE.search(n)]
            if code and not any(TEST_FILE.search(n) for n in names) and session_ran(transcript, TEST_CMD) is False:
                return (f"`git commit` of code ({code[0]}) with no test in it and no test command run in this "
                        "session: write the test alongside the change and run it.")
    return None


def check_bash(cmd, event, s, cfg):
    if not roles.applies("worker_discipline", s):
        return []
    cwd, found = event.get("cwd"), []
    cmds = shell.commands(cmd)
    writes = write_targets(cmds, cwd) + python_heredocs(cmd)
    if writes:
        how = writes[0][0]
        found += fire(cfg, event, "worker.edit_tool",
                      f"A file is written from Bash ({how}). Edit files with the Edit and Write tools: a Bash-made "
                      "patch can pass tests that re-applying it with Edit shows broken.")
    units = top_units(cmds)
    if len(units) > 1 and (verb := next((v for u in units if (v := mutating(u))), None)):
        found += fire(cfg, event, "worker.chain",
                      f"`{verb}` shares one Bash call with other commands. Give each act that changes something its "
                      "own call, so a refusal names it and does not take the others with it.")
    if any(t and MEMORY.search(resolve(t, cwd)) for _, t in writes):
        found += fire(cfg, event, "worker.memory", MEMORY_REASON)
    if reason := pre_push(cmds, event):
        found += fire(cfg, event, "worker.pre_push", reason)
    return found


# ---- Edit, Write, MultiEdit, NotebookEdit -------------------------------------------------------------------------

MEMORY_REASON = ("A card does not write the user's auto-memory (~/.claude/projects/*/memory/): what it learned goes "
                 "in its report, and the user or lead decides what is kept.")


def repo_state(directory):
    """(worktree top, is the main checkout) of the repository holding directory; (None, None) outside one."""
    out = git(directory, "rev-parse", "--show-toplevel", "--absolute-git-dir", "--git-common-dir")
    lines = (out or "").splitlines()
    if len(lines) != 3:
        return None, None
    top, git_dir, common = lines
    common = common if os.path.isabs(common) else os.path.join(directory, common)
    return top, os.path.realpath(git_dir) == os.path.realpath(common)


def nearest_dir(path):
    p = pathlib.Path(path)
    while not p.is_dir() and p != p.parent:
        p = p.parent
    return str(p)


def check_write(args, event, s, cfg):
    if not roles.applies("worker_discipline", s):
        return []
    target = args.get("file_path") or args.get("notebook_path")
    cwd = event.get("cwd")
    if not isinstance(target, str) or not target or not isinstance(cwd, str):
        return []
    full, found = resolve(target, cwd), []
    if MEMORY.search(full):
        return fire(cfg, event, "worker.memory", MEMORY_REASON)
    if scratch(target, cwd):
        return []
    mine, _ = repo_state(cwd)
    if PLUGIN_CACHE.search(full):
        found += fire(cfg, event, "worker.paths",
                      "This path is in the plugin cache, which Claude Code replaces on its next sync: edit the "
                      "checkout the cache came from, in a worktree.")
    elif mine and not under(full, {os.path.realpath(mine)}):
        found += fire(cfg, event, "worker.paths",
                      f"{full} is outside this card's worktree ({mine}). Write inside the worktree, or in a "
                      "temp directory.")
    top, main = repo_state(nearest_dir(full))
    if top and main:
        found += fire(cfg, event, "worker.main_checkout",
                      f"{full} is in the main checkout of {top}, which other sessions load. Edit in a worktree branch.")
    return found
