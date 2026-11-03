"""Commands and writes that lose data, leak a secret or loosen the user's own settings: the checks behind guard.py,
for scripts to call before they run a command themselves (a hook does not see a script's subprocesses).

Each rule has a key under `guard.` in ~/.claude/agent-skills.json and a default level (config.LEVELS). `false` turns
a rule off and leaves the command to the normal permission flow, as before the rule existed.
Commands are read with lib.shell: only a word bash runs as a command counts, so `echo pkill`, a commit message or a
quoted prompt that names one is not one. A guardrail, not a sandbox: `bash -c`, `eval` and a script file's own
commands are not read.
"""
import collections, json, os, pathlib, re, subprocess

from lib import config, shell

Finding = collections.namedtuple("Finding", "key level reason")

ROOT = pathlib.Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or pathlib.Path(__file__).resolve().parents[2])
RULES = {
    "guard.deny.pattern_kill": "deny",
    "guard.deny.prune": "deny",
    "guard.deny.worktree_rm": "deny",
    "guard.deny.automode_reset": "deny",
    "guard.deny.keychain": "deny",
    "guard.deny.url_credentials": "deny",
    "guard.deny.user_settings": "deny",
    "guard.deny.plugin_apply": "deny",
    "guard.deny.slash_send": "deny",
    "guard.deny.prompt_substitution": "deny",
    "guard.deny.self_exempt_label": "deny",
    "guard.deny.owner_only": "deny",
    "guard.deny.external_review": "deny",
    "guard.ask.pressure_bypass": "ask",
    "guard.warn.rm_expansion": "warn",
    "guard.warn.reset_hard": "warn",
    "guard.warn.run_download": "warn",
    "guard.warn.tailscale": "warn",
}
PYTHON = re.compile(r"python(\d+(\.\d+)*)?")
CREDENTIAL_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s@:]+:[^/\s@]+@", re.I)
SLASH = re.compile(r"^\s*/(?:reload\S*|model)\b")
# The docstring line every settings script of this plugin carries, and its emitted copies keep.
APPLY_MARK = re.compile(r"runs --apply, never the agent")
APPLY_SCRIPTS = {"setup.py", "config_emit.py", "automode_rule.py"}
PRUNE_OBJECTS = {"system", "volume", "image", "container", "network", "builder"}
XARGS_OPTS = {"-I", "-i", "-n", "-P", "-L", "-l", "-s", "-E", "-e", "-d", "-a", "--max-args", "--max-procs",
              "--delimiter", "--arg-file", "--max-lines", "--max-chars", "--eof", "--replace"}
# `${VAR:?}` stops bash when VAR is empty, so it cannot widen an rm.
GUARDED_VAR = re.compile(r"\$\{\w+:\?[^}]*\}")
KEY_SOURCE = "(agent-skills hooks/guard.py, {key})"
DOCKER = {"docker", "podman"}
UNKNOWN_DIR = "\0unknown-dir"
# Options that take the next word as their value, before the subcommand.
DOCKER_OPTS = {"-H", "--host", "-c", "--context", "--config", "-l", "--log-level", "--tlscacert", "--tlscert",
               "--tlskey"}
COMPOSE_OPTS = {"-f", "--file", "-p", "--project-name", "--profile", "--env-file", "--project-directory", "--ansi",
                "--progress", "--parallel"}
GIT_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}


def level(cfg, key):
    return config.level(cfg, key, RULES[key])


def user_files():
    """The user's own settings files, real paths: Claude Code user settings, agent-skills.json and secure-fill's
    allowlist. A project's .claude/settings.json is not one."""
    home = pathlib.Path.home()
    dirs = {home / ".claude", pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or home / ".claude").expanduser()}
    files = {d / name for d in dirs for name in ("settings.json", "settings.local.json")}
    files.add(config.path())
    files.add(home / ".claude" / "agent-skills.json")
    files.add(pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "secure-fill" / "allow.json")
    return {os.path.realpath(f) for f in files}


def expand(word, cwd):
    """A path word as bash would mostly expand it: `~`, $HOME and $CLAUDE_CONFIG_DIR, relative to cwd."""
    home = str(pathlib.Path.home())
    for var in ("HOME", "CLAUDE_CONFIG_DIR", "XDG_CONFIG_HOME", "AGENT_SKILLS_CONFIG", "AGENT_SKILLS_STATE"):
        value = os.environ.get(var) or (home if var == "HOME" else "")
        if value:
            word = word.replace("${" + var + "}", value).replace("$" + var, value)
    word = os.path.expanduser(word)
    if cwd is UNKNOWN_DIR:  # after a `cd` this reader cannot follow, only an absolute path is known
        return os.path.realpath(word) if os.path.isabs(word) else ""
    return os.path.realpath(os.path.join(cwd or ".", word))


def after_cd(c, here):
    """The directory a `cd` command leaves the shell in: UNKNOWN_DIR when it cannot be read from the words."""
    args = [x for x in c.words[1:] if not x.startswith("-") or x == "-"]
    target = args[0] if args else "~"
    if target == "-" or "$" in target.replace("$HOME", "").replace("${HOME}", ""):
        return UNKNOWN_DIR
    return expand(target, here) or UNKNOWN_DIR


def verb(args, valued):
    """(the first word of args that is not an option, the words after it); an option in `valued` takes the next
    word as its value. (None, []) when there is none."""
    i = 0
    while i < len(args):
        x = args[i]
        if x == "--":
            return (args[i + 1], args[i + 2:]) if i + 1 < len(args) else (None, [])
        if not x.startswith("-"):
            return x, args[i + 1:]
        i += 2 if x in valued else 1
    return None, []


def inplace_files(c):
    """The files `sed -i` or `perl -i` edits in place: its file operands, the script operand left out unless an
    `-e`/`-f` (sed) or `-e`/`-E` (perl) option, attached or separate, gave the script. [] without in-place editing."""
    perl = c.name == "perl"
    script_opts = "eE" if perl else "ef"
    valued = "IMmx" if perl else "l"  # other short options that take a value
    w, i, files, script, inplace = c.words[1:], 0, [], False, False
    while i < len(w):
        x = w[i]
        i += 1
        if x == "--":
            files += w[i:]
            break
        if x.startswith("--"):
            inplace = inplace or x.startswith("--in-place")
            if x in ("--expression", "--file"):
                script, i = True, i + 1
            script = script or x.startswith(("--expression=", "--file="))
            continue
        if x.startswith("-") and len(x) > 1:
            for k, ch in enumerate(x[1:], 1):
                if ch == "i":  # the rest of the word is a backup suffix
                    inplace = True
                    break
                if ch in script_opts or ch in valued:
                    script = script or ch in script_opts
                    i += 0 if x[k + 1:] else 1
                    break
            continue
        files.append(x)
    if not inplace:
        return []
    return files if script else files[1:]


def write_targets(c):
    """The files a command writes by its own argument or a redirection."""
    out = [t for fd, op, t in c.redirects if op in (">", ">>", ">|", "&>", "&>>", "<>")
           or op == ">&" and not re.fullmatch(r"\d*-?", t)]  # `>&file` writes a file; `>&2`, `>&-` do not
    args = [w for w in c.words[1:] if not w.startswith("-")]
    if c.name == "tee":
        out += args
    elif c.name in ("cp", "mv", "install", "ln", "rsync", "ditto"):
        # `-t DIR` / `--target-directory[=]DIR` names the destination; otherwise it is the last operand.
        target, operands, rest = None, [], c.words[1:]
        for i, x in enumerate(rest):
            if i and rest[i - 1] in ("-t", "--target-directory"):
                target = x
            elif x.startswith("--target-directory="):
                target = x.split("=", 1)[1]
            elif not x.startswith("-"):
                operands.append(x)
        if target is None and len(operands) >= 2:
            target, operands = operands[-1], operands[:-1]
        if target is not None:
            # A destination directory receives each source under its own name; mv also removes its sources.
            out += [target] + [os.path.join(target, os.path.basename(s.rstrip("/"))) for s in operands]
            if c.name == "mv":
                out += operands
    elif c.name in ("sed", "perl"):
        out += inplace_files(c)
    elif c.name == "truncate":
        out += args
    elif c.name == "dd":
        out += [w[3:] for w in c.words if w.startswith("of=")]
    elif c.name in ("rm", "unlink", "chmod"):
        out += args
    return out


def check_command(c, masked, cfg, cwd, session, here=None):
    """[(key, reason)] for one command. `here` is the directory its relative paths resolve against (cwd after the
    line's earlier `cd`s), `cwd` the hook's own."""
    here = cwd if here is None else here
    out, w, name = [], c.words, c.name
    # Before the argv check: a bare `> file` truncates it with no command at all.
    files = user_files()
    if any(expand(t, here) in files for t in write_targets(c) if t):
        out.append(("guard.deny.user_settings", USER_SETTINGS))
    if any(expand(t, here) in owner_files() for t in write_targets(c) if t):
        out.append(("guard.deny.owner_only", OWNER_FILES))
    if not w:
        return out
    if name in ("pkill", "killall"):
        out.append(("guard.deny.pattern_kill", f"`{name}` kills every process whose name matches, other sessions' "
                                               "agents and plugins included. Kill by PID (`kill <pid>` after reading "
                                               "`ps`), or let agent-skills:reap-resources (reap.py) pick the targets."))
    if name in DOCKER or name == "docker-compose":
        sub, rest = verb(w[1:], DOCKER_OPTS) if name in DOCKER else ("compose", w[1:])
        if sub in PRUNE_OBJECTS and verb(rest, set())[0] == "prune":
            out.append(("guard.deny.prune", f"`{' '.join(w[:4])}` deletes what any project on this machine left "
                                            "stopped or unused, other workers' stacks and volumes included. Remove "
                                            "your own containers or volumes by name."))
        down, rest = verb(rest, COMPOSE_OPTS) if sub == "compose" else (None, [])
        rest = rest[:rest.index("--")] if "--" in rest else rest
        if down == "down" and any(x in ("--volumes", "--volumes=true") or re.fullmatch(r"-[a-zA-Z]*v[a-zA-Z]*", x)
                                  for x in rest):
            out.append(("guard.deny.prune", "`compose down -v` deletes the project's named volumes, data another "
                                            "worker or the human may still use. Run `down` without -v; remove a "
                                            "volume you created by name."))
    if name == "git":
        sub, rest = verb(w[1:], GIT_OPTS)
        if sub == "worktree" and verb(rest, set())[0] == "prune":
            out.append(("guard.deny.prune", "`git worktree prune` drops the records of every worktree whose folder is "
                                            "missing right now, other sessions' included. Remove one worktree by "
                                            "path with `git worktree remove <path>`."))
    if name == "orca" and w[1:3] in (["worktree", "rm"], ["worktree", "remove"]):
        if "--force" in w or "--allow-failed-archive-hook" in w:
            out.append(("guard.deny.worktree_rm", "`orca worktree rm --force` (or --allow-failed-archive-hook) removes "
                                                  "a worktree with work Orca would keep. Commit or hand off the work "
                                                  "and run it without the flag; agent-skills:end-session says when."))
        elif current_main(w, cwd):
            out.append(("guard.deny.worktree_rm", "`orca worktree rm --worktree current` here removes the main "
                                                  "worktree of the repository."))
    if name == "claude" and [x for x in w[1:] if not x.startswith("-")][:2] == ["auto-mode", "reset"]:
        out.append(("guard.deny.automode_reset", "`claude auto-mode reset` drops the user's auto mode rules. Only the "
                                                 "user resets them; agent-skills:tune-automode drafts a change."))
    if name == "security" and len(w) > 1 and (w[1] == "dump-keychain" or re.fullmatch(r"find-[\w-]*password", w[1])
                                              and any(x in ("-w", "-g") or re.fullmatch(r"-[a-zA-Z]*[wg][a-zA-Z]*", x)
                                                      for x in w[2:])):
        out.append(("guard.deny.keychain", "This prints a Keychain secret into the session. Use "
                                           "agent-skills:secure-fill, which hands it to the page without showing it."))
    if any(CREDENTIAL_URL.search(x) for x in w[1:]):
        out.append(("guard.deny.url_credentials", "A URL with `user:secret@` puts the credential in the command line, "
                                                  "the process list and the transcript. Use the credential helper or "
                                                  "an authenticated CLI (`gh`) instead."))
    if PYTHON.fullmatch(name) or name.endswith(".py"):
        script = name_script(w)
        if script and plugin_apply(script, w, here):
            out.append(("guard.deny.plugin_apply", f"`{os.path.basename(script)}` with --apply/--write changes the "
                                                   "user's settings or scheduled jobs; the user runs that line, never "
                                                   "the agent. Hand them the exact command."))
    if name == "orca" and w[1:3] == ["terminal", "send"] and any(SLASH.match(x) for x in w[3:]):
        out.append(("guard.deny.slash_send", "Typing `/reload-…` or `/model` into another session changes it under "
                                             "its own turn. Use `skills-sync broadcast` (reload) or ask its owner."))
    if name == "gh" and "no-visual-change" in " ".join(w):
        if label_add(w):
            out.append(("guard.deny.self_exempt_label", "The `no-visual-change` label exempts a PR from visual review; "
                                                        "an agent never adds it to its own PR. Ask the human."))
    if name == "orch" and (w[1:3] == ["refusal", "clear"] or w[1:2] == ["exempt"]):
        out.append(("guard.deny.owner_only", f"`orch {' '.join(w[1:3])}` is the human's to run, as a `!` line in their "
                                             "own chat."))
    if name == "codex" and (reason := external_review(w, here, cfg)):
        out.append(("guard.deny.external_review", reason))
    if any(x == "AGENT_SKILLS_PRESSURE_GATE=0" for x in c.assigns + (w[1:] if name == "export" else [])):
        out.append(("guard.ask.pressure_bypass", "This turns the pressure gate off for one command; only the human "
                                                 "approves that."))
    if name == "rm" and re.search(r"[*?$\[]", GUARDED_VAR.sub("", c.masked.split("rm", 1)[-1])):
        out.append(("guard.warn.rm_expansion", "`rm` with an unquoted glob or variable can widen past what you meant "
                                               "(an empty variable is the parent directory). Write the paths out."))
    if name == "git" and "reset" in w and "--hard" in w and dirty(w, cwd):
        out.append(("guard.warn.reset_hard", "`git reset --hard` here drops uncommitted changes. Commit them to a WIP "
                                             "commit first."))
    if name == "gh" and w[1:3] == ["run", "download"] and not any(x in ("-D", "--dir") or x.startswith("--dir=")
                                                                   for x in w[3:]):
        out.append(("guard.warn.run_download", "`gh run download` without -D unpacks into the current directory. Give "
                                               "it an empty directory outside the worktree."))
    if name == "tailscale" and len(w) > 1 and w[1] in ("serve", "funnel") and session and session.role == "worker":
        out.append(("guard.warn.tailscale", f"`tailscale {w[1]}` from a worker changes what this machine publishes "
                                            "for every session. Leave it to the human or the lead."))
    return out


USER_SETTINGS = ("This writes the user's own settings (Claude Code user settings, ~/.claude/agent-skills.json or "
                 "secure-fill's allowlist). The user changes them: agent-skills:skills-config or tune-automode emits "
                 "a script the user runs.")
OWNER_FILES = ("This writes the refused-act ledger (refusals.jsonl in the agent-skills state directory). Only the human "
               "changes it, with `!orch refusal clear <id>`; `orch refusal report` reads it.")


def owner_files():
    """Real paths of the files only the human changes: the refused-act ledger."""
    return {os.path.realpath(config.state() / "refusals.jsonl")}


def name_script(w):
    """The script a python command or a directly run .py file runs; None for `-c`/`-m`."""
    if w[0].endswith(".py"):
        return w[0]
    for x in w[1:]:
        if x in ("-c", "-m"):
            return None
        if not x.startswith("-"):
            return x
    return None


def plugin_apply(script, w, cwd):
    full = pathlib.Path(expand(script, cwd))
    if full.name == "bootstrap.py" and "--write" in w:
        return full.parent.name == "scripts" and (full.parents[1] / "skills" / "setup-pc").is_dir()
    if not any(x == "--apply" or x.startswith("--apply=") for x in w):
        return False
    if full.name in APPLY_SCRIPTS and full.is_relative_to(ROOT.resolve()):
        return True
    try:
        with open(full, encoding="utf-8", errors="replace") as f:
            return bool(APPLY_MARK.search(f.read(4096)))
    except OSError:
        return False


def label_add(w):
    def has(values):
        return any("no-visual-change" in v.split(",") for v in values)
    vals = [w[i + 1] for i, x in enumerate(w[:-1]) if x in ("--label", "-l", "--add-label")]
    vals += [x.split("=", 1)[1] for x in w if x.startswith(("--label=", "--add-label="))]
    if w[1:3] in (["pr", "create"], ["pr", "edit"], ["issue", "edit"], ["issue", "create"]) and has(vals):
        return True
    if w[1:2] != ["api"]:
        return False
    # Only a request that adds labels: POST/PUT/PATCH (a field without --method means POST) to …/labels, with the
    # label in a field. A GET that filters on it or a DELETE of it is not one.
    method = next((w[i + 1] for i, x in enumerate(w[:-1]) if x in ("-X", "--method")), None)
    method = next((x.split("=", 1)[1] for x in w if x.startswith("--method=")), method)
    fields = [w[i + 1] for i, x in enumerate(w[:-1]) if x in ("-f", "-F", "--field", "--raw-field")]
    fields += [x.split("=", 1)[1] for x in w if x.startswith(("--field=", "--raw-field="))]
    method = (method or ("POST" if fields or "--input" in w else "GET")).upper()
    endpoint = verb(w[2:], {"-X", "--method", "-f", "-F", "--field", "--raw-field", "-H", "--header", "--input",
                            "-q", "--jq", "-t", "--template", "--hostname", "--cache", "-p", "--preview"})[0] or ""
    return (method in ("POST", "PUT", "PATCH") and re.search(r"/labels/?$", endpoint) is not None
            and has(f.split("=", 1)[-1] for f in fields))


def run(argv, cwd, timeout=3):
    return subprocess.run(argv, cwd=cwd or None, capture_output=True, text=True, timeout=timeout)


def current_main(w, cwd):
    """`orca worktree rm --worktree current` in the main worktree; False when Orca does not say."""
    i = w.index("--worktree") if "--worktree" in w else -1
    if not (i >= 0 and w[i + 1:i + 2] == ["current"] or "--worktree=current" in w):
        return False
    try:
        data = json.loads(run(["orca", "worktree", "current", "--json"], cwd).stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False

    def find(node):
        if isinstance(node, dict):
            return node.get("isMainWorktree") is True or any(find(v) for v in node.values())
        return isinstance(node, list) and any(find(v) for v in node)
    return find(data)


def dirty(w, cwd):
    where = w[w.index("-C") + 1] if "-C" in w[:-1] else cwd
    try:
        return bool(run(["git", "status", "--porcelain", "--untracked-files=no"], where).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def repo_of(path):
    """owner/repo of the git checkout at path, from its origin URL; None when unknown."""
    try:
        url = run(["git", "remote", "get-url", "origin"], path).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def external_review(argv, cwd, cfg):
    """Why the repository codex would read must not go to an outside LLM, or None. Only a repository whose
    `repos.<owner/repo>.external_llm` is false is refused; no key, no decision. Review wrappers call this too."""
    repos = cfg.get("repos") if isinstance(cfg.get("repos"), dict) else {}
    closed = {k for k, v in repos.items() if isinstance(v, dict) and v.get("external_llm") is False}
    if not closed:
        return None
    where = cwd
    for i, x in enumerate(argv):
        if x in ("-C", "--cd") and i + 1 < len(argv):
            where = expand(argv[i + 1], cwd)
        elif x.startswith("--cd="):
            where = expand(x[5:], cwd)
        elif x.startswith("-C") and len(x) > 2 and not x.startswith("--"):
            where = expand(x[2:], cwd)
    if where in (UNKNOWN_DIR, ""):  # after a `cd` this reader cannot follow: no decision
        return None
    repo = repo_of(where)
    if repo in closed:
        return (f"{repo} sets `repos.{repo}.external_llm: false` in ~/.claude/agent-skills.json: its code does not go "
                "to an outside model. Review it with a Claude subagent instead.")
    return None


def upstream(cmds, i):
    """The commands that feed cmds[i] through its own pipeline."""
    out = []
    while cmds[i].piped_from and i > 0 and cmds[i - 1].piped_to:
        i -= 1
        out.append(cmds[i])
    return out


def check_bash(cmd, cfg, cwd=None, session=None):
    """[Finding] for a Bash command, rules turned off left out."""
    masked = shell.command_text(cmd)
    out, seen = [], set()
    cmds, here = shell.commands(cmd), cwd
    for i, c in enumerate(cmds):
        found = check_command(c, masked, cfg, cwd, session, here)
        if c.name == "cd":
            here = after_cd(c, here)
        # `kill $(pgrep …)`: a pgrep inside this kill command's own substitution.
        if c.name == "kill" and any(d.name == "pgrep" and c.span[0] < d.span[0] and d.span[1] <= c.span[1]
                                    for d in cmds):
            found.append(("guard.deny.pattern_kill", "`kill $(pgrep …)` kills every process whose name matches. Kill "
                                                     "by PID after reading `ps`, or use agent-skills:reap-resources."))
        # `pgrep … | xargs kill`: a pgrep earlier in this xargs command's own pipeline.
        if c.name == "xargs" and os.path.basename(verb(c.words[1:], XARGS_OPTS)[0] or "") == "kill" and any(
                d.name == "pgrep" for d in upstream(cmds, i)):
            found.append(("guard.deny.pattern_kill", "`pgrep … | xargs kill` kills every process whose name matches. "
                                                     "Kill by PID after reading `ps`, or use agent-skills:reap-resources."))
        for key, reason in found:
            lvl = level(cfg, key)
            reason = reason.replace(shell.TEXT_DOLLAR, "$").replace(shell.TEXT_TILDE, "~")
            if lvl != "off" and (key, reason) not in seen:
                seen.add((key, reason))
                out.append(Finding(key, lvl, f"{reason} {KEY_SOURCE.format(key=key)}"))
    # `--prompt "$(cat f)"`: the card's first prompt is whatever the file held when the command ran. shell.lift left
    # a `$___` word where each substitution was; one inside a quoted prompt is text, written with a stand-in `$`.
    prompts = [v for c in cmds if c.name == "orca" and c.words[1:3] == ["worktree", "create"]
               for i, x in enumerate(c.words)
               for v in ([c.words[i + 1]] if x == "--prompt" and i + 1 < len(c.words) else
                         [x.split("=", 1)[1]] if x.startswith("--prompt=") else [])]
    if any(re.search(r"\$_{2,}", v) for v in prompts):
        key = "guard.deny.prompt_substitution"
        if (lvl := level(cfg, key)) != "off":
            out.append(Finding(key, lvl, "The card's --prompt runs a command substitution, so nothing checks what it "
                                         "puts there. Create the card with `skills-sync card <brief>`, which reads "
                                         f"and checks the brief file. {KEY_SOURCE.format(key=key)}"))
    return out


def check_write(path, cfg):
    """[Finding] for an Edit, Write or NotebookEdit of path."""
    if not isinstance(path, str) or not path:
        return []
    real = os.path.realpath(os.path.expanduser(path))
    out = []
    for key, files, reason in (("guard.deny.user_settings", user_files(), USER_SETTINGS),
                               ("guard.deny.owner_only", owner_files(), OWNER_FILES)):
        if (lvl := level(cfg, key)) != "off" and real in files:
            out.append(Finding(key, lvl, f"{reason} {KEY_SOURCE.format(key=key)}"))
    return out
