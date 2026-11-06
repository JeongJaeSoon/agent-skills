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
import datetime, json, os, pathlib, re, subprocess, time

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
# A file mode is at most four characters; the bound keeps a long quoted run from backtracking.
MODE = r"""(?:mode\s*=\s*)?['"](?=[rwxabt+]{0,3}[wax+])[rwxabt+]{1,4}['"]"""
PYTHON_MODE = re.compile(rf"""\s*{MODE}""")
# Calls in python source (strings and comments masked out): `.write_text(`, `Path(p).open("w")`, `open(path, "w")`.
PYTHON_CALL = re.compile(r"\.write_(?:text|bytes)\(|\.open\(|\bopen\(")
OPEN_SCAN = 400
SED_INPLACE = re.compile(r"-[A-Za-z]*i|--in-place(?:=.*)?$")
SED_VALUE_OPTS = {"-e", "-f", "--expression", "--file"}
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
    try:
        p = pathlib.Path(path).expanduser()
    except (RuntimeError, KeyError):  # `~nobody-by-that-name/…`: left as written
        p = pathlib.Path(path)
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

def write_targets(cmds, cwd, after_cd):
    """[(how, target)] of the file writes the commands make, scratch targets left out. target is None when the
    command writes a file this reader cannot name (a python snippet)."""
    out = []
    for c in cmds:
        cwd = after_cd.get(id(c), cwd)
        for fd, op, target in c.redirects:
            if op == ">&" and (target.isdigit() or target == "-"):
                continue
            if (op in WRITE_OPS or op == ">&") and not scratch(target, cwd):
                out.append((f"`{fd}{op}` redirect", resolve(target, cwd)))
        w = c.words
        if c.name == "tee":
            out += [("`tee`", resolve(f, cwd)) for f in w[1:] if not f.startswith("-") and not scratch(f, cwd)]
        elif c.name == "sed" and any(SED_INPLACE.match(x) for x in w[1:]):
            files = sed_files(w)
            if bad := next((f for f in files if not scratch(f, cwd)), None):
                out.append(("`sed -i`", resolve(bad, cwd)))
        elif PYTHON.fullmatch(c.name) and "-c" in w[1:-1]:
            if python_writes(w[w.index("-c") + 1]):
                out.append(("`python -c` file write", None))
    return out


def sed_files(words):
    """The file operands of a sed command: its words minus options, option values and the script (the first operand
    unless -e or -f gave one)."""
    rest, skip, scripted = [], False, False
    for x in words[1:]:
        if skip:
            skip = False
        elif x == "":  # BSD `sed -i '' script file`: the empty backup suffix is no operand
            continue
        elif x in SED_VALUE_OPTS:
            skip = scripted = True
        elif x.startswith(("--expression=", "--file=", "-e")):
            scripted = True
        elif not x.startswith("-"):
            rest.append(x)
    return rest if scripted else rest[1:]


def code_mask(code):
    """The python source with each comment and string body blanked to NUL (quotes and length kept), so a call named in
    a string or a comment is not a call."""
    out, i, n = [], 0, len(code)
    while i < n:
        c = code[i]
        if c == "#":
            j = code.find("\n", i)
            j = n if j < 0 else j
            out.append("\0" * (j - i))
        elif c in "'\"":
            q = code[i:i + 3] if code[i:i + 3] in ("'''", '"""') else c
            j = i + len(q)
            while j < n and not code.startswith(q, j):
                j += 2 if code[j] == "\\" else 1
            j, closed = (j + len(q), True) if j < n else (n, False)
            out.append(q + "\0" * max(j - i - len(q) - (len(q) if closed else 0), 0) + (q if closed else ""))
        else:
            out.append(c)
            i += 1
            continue
        i = j
    return "".join(out)[:n]


def first_arg_end(mask, start):
    """Index of the first top-level comma (or the closing parenthesis) of the call whose arguments begin at start,
    scanning at most OPEN_SCAN characters of the masked source; the mode argument follows the comma."""
    depth, i, end = 1, start, min(len(mask), start + OPEN_SCAN)
    while i < end:
        c = mask[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if not depth:
                return i, False
        elif c == "," and depth == 1:
            return i, True
        i += 1
    return None, False


def receiver_start(mask, pos):
    """Where the expression ending at pos begins: a dotted name, optionally with call or index brackets
    (`Path('x')`, `a.b[0]`), found backwards over the masked source."""
    i, depth = pos, 0
    while i > 0:
        c = mask[i - 1]
        if c in ")]":
            depth += 1
        elif c in "([":
            if not depth:
                break
            depth -= 1
        elif not depth and not (c.isalnum() or c in "_."):
            break
        i -= 1
        if i < pos - 300:
            break
    return i


def in_temp(text):
    return any(f"{q}{r}" in text for r in TEMP_ROOTS for q in "'\"")


def python_writes(code):
    """True when the python source, as code, opens a file for writing. A write whose own path (the first argument of
    `open(`, the receiver of `.write_text(` or `.open(`) is a temp-directory literal is left out."""
    mask = code_mask(code)
    for m in PYTHON_CALL.finditer(mask):
        text, name = m.group(), m.group().lstrip(".")
        if name.startswith("write_"):
            opens = True
        elif text.startswith("."):  # Path(p).open("w"): the mode is the first argument, the receiver a call
            receiver = code[receiver_start(mask, m.start()):m.start()]
            # A call (`Path(p).open`) or a name the code bound to a Path; `io.open(path, mode)` and its kin are not it.
            is_path = receiver[-1:] in (")", "]") or re.search(
                rf"\b{re.escape(receiver)}\s*=\s*(?:\w+\.)*Path\(", code) is not None if receiver.isidentifier() or receiver[-1:] in (")", "]") else False
            opens = is_path and bool(PYTHON_MODE.match(code, m.end()))
        else:  # open(path, "w"): the mode follows a top-level comma
            comma, more = first_arg_end(mask, m.end())
            opens = more and bool(PYTHON_MODE.match(code, comma + 1))
        if not opens:
            continue
        if text.startswith(".") or name.startswith("write_"):
            path = code[receiver_start(mask, m.start()):m.start()]
        else:
            path = code[m.end():comma]
        if not in_temp(path):
            return True
    return False


def heredoc_bodies(cmd, after, heredocs):
    """The bodies, in order, of the heredocs `[(op, delimiter)]` opened on the line holding offset `after`: each starts
    where the one before ended. A body with no delimiter line is None, and so are the ones after it."""
    start = cmd.find("\n", after)
    if start < 0:
        return [None] * len(heredocs)
    lines, at, out = cmd[start + 1:].split("\n"), 0, []
    for op, delim in heredocs:
        body = None
        for i in range(at, len(lines)):
            if (lines[i].lstrip("\t") if op == "<<-" else lines[i]) == delim:
                body, at = "\n".join(lines[at:i]), i + 1
                break
        out.append(body)
        if body is None:
            out += [None] * (len(heredocs) - len(out))
            break
    return out


def python_heredocs(cmds, cmd):
    """[(how, None)] for each python command bash runs with a heredoc on stdin whose body opens a file for writing.
    Only a parsed command counts: a `python3 <<PY` line inside another command's heredoc body is text."""
    out = []
    for c in cmds:
        # Stdin is the program only without `-c`/`-m` and a script operand (`python3 -` or bare).
        reads_stdin = not {"-c", "-m"} & set(c.words[1:]) and [w for w in c.words[1:] if not w.startswith("-") or w == "-"] in ([], ["-"])
        if PYTHON.fullmatch(c.name) and reads_stdin:
            rs = c.redirects
            stdin = [i for i, (fd, op, _) in enumerate(rs) if fd in ("", "0") and op in ("<", "<<", "<<-", "<>")]
            here = [(op, delim) for _, op, delim in rs if op in ("<<", "<<-")]  # every heredoc, any fd
            # The last stdin redirect is the one the program reads; its body is the one at its place among the heredocs.
            if stdin and rs[stdin[-1]][1] in ("<<", "<<-"):
                nth = sum(1 for _, op, _ in rs[:stdin[-1]] if op in ("<<", "<<-"))
                if (body := heredoc_bodies(cmd, c.span[1], here)[nth]) is not None and python_writes(body):
                    out.append(("python heredoc file write", None))
    return out


def cd_targets(cmds, cwd, cmd):
    """id(command) -> the directory it runs in, following the `cd <dir>` commands before it in a call. A `cd` moves the
    shell it runs in: inside `( … )` it holds until the closing parenthesis, and one in a `$(…)` or a pipeline moves
    nothing."""
    masked = shell.read(cmd)[0]
    # The `)` that ends a `case` pattern closes nothing: leave it out of the parenthesis count.
    no_case = shell.case_patterns(masked)
    case_close = {masked.find(")", i + 1) for i in range(len(masked) - 1)
                  if masked[i] != no_case[i] and masked[i + 1] == no_case[i + 1]}
    nested = {id(c) for c in cmds if any(o is not c and o.span[0] <= c.span[0] and c.span[1] <= o.span[1]
                                         and o.span != c.span for o in cmds)}
    out, stack, pos = {}, [cwd], 0  # one directory per open parenthesis: a `(` copies, a `)` drops
    for c in sorted(cmds, key=lambda c: c.span[0]):
        for at in range(pos, c.span[0]):
            if no_case[at] == "(":  # no_case has the patterns, optional `(` included, blanked
                stack.append(stack[-1])
            elif no_case[at] == ")" and len(stack) > 1 and at not in case_close:
                stack.pop()
        pos = c.span[0]
        out[id(c)] = stack[-1]
        if c.name == "cd" and len(c.words) == 2 and c.words[1] != "-" and not c.words[1].startswith(("$", "-")) \
                and id(c) not in nested and not (c.piped_from or c.piped_to) \
                and not masked[c.span[1]:].lstrip().startswith("|"):
            stack[-1] = resolve(c.words[1], stack[-1])
    return out


def top_units(cmds):
    """The commands a call runs one after another: those not inside another command's span (a `$(…)` body is part
    of its outer command). Returns (top-level commands, how many of them start a pipeline or stand alone)."""
    tops = [c for c in cmds if not any(o is not c and o.span[0] <= c.span[0] and c.span[1] <= o.span[1]
                                       and o.span != c.span for o in cmds)]
    return tops, sum(1 for c in tops if not c.piped_from)


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
    if (verb := git_verb(w)) in MUTATING_GIT and not (flag_given(w, "--dry-run") or verb == "push" and flag_given(w, "-n")):
        return f"git {verb}"
    if c.name == "orca" and "worker_done" in w:
        return "worker_done"
    return None


BUDGET_S = 2.0
# A command that mentions a test but runs none.
NOT_A_RUN = {"echo", "printf", "git", "grep", "rg", "cat", "head", "tail", "ls", "sed", "awk", "wc"}


def history(transcript, current_id):
    """The commands of the session's earlier Bash calls, [[Command]] per call, the call being judged left out (by its
    tool_use id). None when the transcript cannot be read in time."""
    start, calls = time.monotonic(), []
    try:
        if not transcript or os.path.getsize(transcript) > TRANSCRIPT_MAX:
            return None
        with open(transcript, encoding="utf-8", errors="replace") as f:
            for raw in f:
                if '"Bash"' not in raw or not ("push" in raw or TEST_CMD.search(raw)):
                    continue
                if time.monotonic() - start > BUDGET_S:
                    return None
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                content = (entry.get("message") or {}).get("content") if isinstance(entry, dict) else None
                for b in content if isinstance(content, list) else ():
                    if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Bash":
                        calls.append((b.get("id"), str((b.get("input") or {}).get("command") or "")))
    except (OSError, AttributeError, TypeError):  # a record shaped otherwise is a transcript this cannot read
        return None
    # Claude Code gives a PreToolUse event the call's tool_use_id; without one the transcript is taken as it stands.
    past = []
    for i, c in calls:
        if current_id and i == current_id:
            continue
        try:
            past.append(shell.commands(c))
        except Exception:  # a command the reader cannot parse (nesting deeper than recursion allows) is no evidence
            past.append([])
    return past


def ran_test(past):
    return any(c.name not in NOT_A_RUN and TEST_CMD.search(" ".join(c.words)) for cs in past for c in cs)


def pushed(past):
    return any(git_verb(c.words) == "push" for cs in past for c in cs)


def git_cwd(words, cwd):
    """The directory `git -C dir verb …` works in, else cwd: a `-C` after the verb (`commit -C <commit>`) is the
    verb's own."""
    i = 1
    while i < len(words) and (words[i] in GIT_OPTS or words[i].startswith("-")):
        if words[i] == "-C" and i + 1 < len(words):
            cwd = resolve(words[i + 1], cwd)
        i += 2 if words[i] in GIT_OPTS else 1
    return cwd


def flag_given(words, flag):
    """The word is an option of its own, not the value of `-m` and the like."""
    def value_of_previous(i):
        prev = words[i - 1] if i else ""
        return prev in COMMIT_VALUE_OPTS or takes_next(prev)

    return any(w == flag and not value_of_previous(i) for i, w in enumerate(words))


SHORT_CLUSTER = re.compile(r"-([aenqsSuvzioPp]*)(?:([mFCct])(.*))?")  # flags, then a flag taking a value, its text


def takes_next(word):
    """A `git commit` short-option word such as `-m` or `-am` whose last flag takes the next word as its value;
    `-mtext` carries its value and `-a` takes none."""
    m = SHORT_CLUSTER.fullmatch(word) if word != "-" else None
    return bool(m and m.group(2) and not m.group(3))
COMMIT_VALUE_OPTS = {"-m", "-F", "-C", "-c", "-t", "--message", "--file", "--author", "--date", "--template",
                     "--reuse-message", "--reedit-message", "--cleanup", "--fixup", "--squash", "--trailer"}


def commit_names(words, cwd):
    """The file names a `git commit` will record: `-a` takes the tracked changes, a pathspec only those paths, else
    the index."""
    rest, paths, all_tracked, skip = words[words.index("commit") + 1:], [], False, False
    for i, w in enumerate(rest):
        if skip:
            skip = False
        elif w == "--":
            paths += rest[i + 1:]
            break
        elif w in COMMIT_VALUE_OPTS:
            skip = True
        elif w == "--all":
            all_tracked = True
        elif w != "-" and (m := SHORT_CLUSTER.fullmatch(w)):  # `-am` is flags and a message; `-amtext` carries its own
            all_tracked = all_tracked or "a" in m.group(1)
            skip = takes_next(w)
        elif not w.startswith("-"):
            paths.append(w)
    if paths:
        out = git(cwd, "diff", "HEAD", "--name-only", "--", *paths)
    elif all_tracked:
        out = git(cwd, "diff", "HEAD", "--name-only")
    else:
        out = git(cwd, "diff", "--cached", "--name-only")
    return (out or "").split()


def pre_push(cmds, event, after_cd):
    """The reason `git rebase|push|commit` is early, None when it is not."""
    transcript, past = event.get("transcript_path"), None
    for c in cmds:
        verb, w = git_verb(c.words), c.words
        if verb not in ("rebase", "push", "commit") or flag_given(w, "--dry-run") \
                or verb == "push" and flag_given(w, "-n"):
            continue
        cwd = git_cwd(w, after_cd.get(id(c), event.get("cwd")))
        if verb == "rebase":
            if {"--abort", "--continue", "--skip", "--quit"} & set(w):
                continue
            branch = (git(cwd, "branch", "--show-current") or "").strip()
            if branch and git(cwd, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}") is not None:
                return (f"`git rebase` of `{branch}`, which origin already has: a pushed branch takes a change on "
                        "main by merging main in, because a rebase needs a force-push.")
            continue
        if past is None:
            past = history(transcript, event.get("tool_use_id"))
        if past is None or ran_test(past):
            continue
        if verb == "push":
            if not pushed(past):
                return "The first `git push` of this session and no test command has run in it: run the tests first."
        else:
            names = commit_names(w, cwd)
            code = [n for n in names if CODE_FILE.search(n) and not TEST_FILE.search(n)]
            if code and not any(TEST_FILE.search(n) for n in names):
                return (f"`git commit` of code ({code[0]}) with no test in it and no test command run in this "
                        "session: write the test alongside the change and run it.")
    return None


def check_bash(cmd, event, s, cfg):
    if not roles.applies("worker_discipline", s):
        return []
    cwd, found = event.get("cwd"), []
    cmds = shell.commands(cmd)
    after_cd = cd_targets(cmds, cwd, cmd)
    writes = write_targets(cmds, cwd, after_cd) + python_heredocs(cmds, cmd)
    if writes:
        how = writes[0][0]
        found += fire(cfg, event, "worker.edit_tool",
                      f"A file is written from Bash ({how}). Edit files with the Edit and Write tools: a Bash-made "
                      "patch can pass tests that re-applying it with Edit shows broken.")
    tops, units = top_units(cmds)
    if units > 1 and (verb := next((v for u in tops if (v := mutating(u))), None)):
        found += fire(cfg, event, "worker.chain",
                      f"`{verb}` shares one Bash call with other commands. Give each act that changes something its "
                      "own call, so a refusal names it and does not take the others with it.")
    if any(t and MEMORY.search(t) for _, t in writes):
        found += fire(cfg, event, "worker.memory", MEMORY_REASON)
    if reason := pre_push(cmds, event, after_cd):
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
