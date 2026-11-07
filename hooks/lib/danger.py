"""Commands and writes that lose data, leak a secret or loosen the user's own settings: the checks behind guard.py,
for scripts to call before they run a command themselves (a hook does not see a script's subprocesses).

Each rule has a key under `guard.` in ~/.claude/agent-skills.json and a default level (config.LEVELS). `false` turns
a rule off and leaves the command to the normal permission flow, as before the rule existed.
Commands are read with lib.shell: only a word bash runs as a command counts, so `echo pkill`, a commit message or a
quoted prompt that names one is not one. A guardrail, not a sandbox: `bash -c`, `eval` and a script file's own
commands are not read.
"""
import collections, fnmatch, glob, json, os, pathlib, re, subprocess, types

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
    "guard.deny.rm_outside": "deny",
    "guard.deny.trash_empty": "deny",
    "guard.deny.protection_write": "deny",
    "guard.deny.default_force_push": "deny",
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
# git accepts any unique prefix of a long option, so `--force-wi` is --force-with-lease.
PUSH_LONG = ["--all", "--atomic", "--branches", "--delete", "--dry-run", "--exec", "--follow-tags", "--force",
             "--force-if-includes", "--force-with-lease", "--mirror", "--no-dry-run", "--no-force",
             "--no-force-with-lease", "--no-verify", "--porcelain", "--progress", "--prune", "--push-option", "--quiet",
             "--receive-pack", "--recurse-submodules", "--repo", "--set-upstream", "--signed", "--tags", "--thin",
             "--verbose"]
PUSH_VALUED = {"--push-option", "--repo", "--receive-pack", "--exec", "--recurse-submodules"}
# Options that take a value, per tool: the short letters (the rest of a cluster, or the next word, is the value, so
# in `-sG` the letters are flags and in `-AGitHub` the rest is the User-Agent) and the long names. They differ: gh's
# `-f` takes a field, curl's `-f` is a flag.
GH_SHORT = "XFfHqtp"
GH_LONG = {"--method", "--field", "--raw-field", "--header", "--input", "--jq", "--template", "--hostname", "--cache",
           "--preview"}
CURL_SHORT = "AbcCdDeEFHKmoPQrtTuUwxXyYz"
CURL_DATA = {"-d", "--data", "--data-ascii", "--data-raw", "--data-binary", "--data-urlencode", "--json", "-F",
             "--form", "--form-string", "-T", "--upload-file"}
CURL_LONG = CURL_DATA - set("-d -F -T".split()) | {
    "--header", "--proxy-header", "--user-agent", "--referer", "--user", "--output", "--cookie", "--cookie-jar",
    "--request", "--request-target", "--url", "--proto-default", "--max-time", "--connect-timeout", "--retry",
    "--proxy", "--cert", "--key", "--cacert", "--resolve", "--write-out", "--config", "--dump-header",
    "--oauth2-bearer", "--limit-rate", "--interface"}


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


def keep_name(path):
    """path with its folder resolved and its last name kept as written: rm removes a symlink, not its target."""
    if path.endswith("/") or os.path.basename(path) in ("", ".", ".."):
        return os.path.realpath(path)
    return os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))


def expand(word, cwd, follow=True):
    """A path word as bash would mostly expand it: `~`, $HOME and $CLAUDE_CONFIG_DIR, relative to cwd. With
    follow=False a symlink named last is not resolved."""
    home = str(pathlib.Path.home())
    for var in ("HOME", "CLAUDE_CONFIG_DIR", "XDG_CONFIG_HOME", "AGENT_SKILLS_CONFIG", "AGENT_SKILLS_STATE"):
        value = os.environ.get(var) or (home if var == "HOME" else "")
        if value:
            word = word.replace("${" + var + "}", value).replace("$" + var, value)
    word = os.path.expanduser(word)
    if cwd is UNKNOWN_DIR:  # after a `cd` this reader cannot follow, only an absolute path is known
        word = word if os.path.isabs(word) else ""
    elif word:
        word = os.path.join(cwd or ".", word)
    return (os.path.realpath(word) if follow else keep_name(word)) if word else ""


def after_cd(c, here, prev=UNKNOWN_DIR):
    """The directory a `cd` command leaves the shell in: UNKNOWN_DIR when it cannot be read from the words.
    `prev` is the directory before the last directory change, which `cd -` returns to."""
    args = [x for x in c.words[1:] if not x.startswith("-") or x == "-"]
    target = args[0] if args else "~"
    # `popd`, a bare `pushd` and `pushd +1` take the directory from a stack this reader does not keep.
    if c.name == "popd" or c.name == "pushd" and (not args or args[0][0] in "+-"):
        return UNKNOWN_DIR
    if target == "-" and c.name == "cd":
        return prev
    if target == "-" or "$" in target.replace("$HOME", "").replace("${HOME}", ""):
        return UNKNOWN_DIR
    return expand(target, here) or UNKNOWN_DIR


# Options of switch and checkout that do not change which branch they name.
SWITCH_PLAIN = {"-q", "--quiet", "-f", "--force", "-m", "--merge", "--discard-changes", "--guess", "--no-guess",
                "--progress", "--no-progress", "--ignore-other-worktrees"}
# A switch whose branch cannot be read: a later forced push of HEAD is treated as aimed at the default branch.
SWITCH_UNKNOWN = "\0unknown-branch"
ANY_REPO = "\0any-repository"  # the key of a switch that ran under --git-dir or --work-tree: it may be any repository


def after_switch(c, switched, here):
    """{repository: branch} after a `git switch <b>`, `git checkout <b>` or `-c/-b <b> [<start>]` on the same command
    line: the branch it leaves checked out in the repository it ran in (SWITCH_UNKNOWN when that cannot be read).
    `switched` is the map before this command; it is returned unchanged for any other."""
    sub, args = verb(c.words[1:], GIT_OPTS)
    if sub not in ("switch", "checkout"):
        return switched
    # `checkout <b> -- <paths>` restores files and keeps HEAD; `checkout <b> --` and `switch -- <b>` switch.
    head, sep, tail = (args[:args.index("--")], True, args[args.index("--") + 1:]) if "--" in args else (args, False, [])
    if sep and sub == "checkout" and tail:
        return switched
    words = head + (tail if sub == "switch" else [])
    # `git -C <dir> switch` moves HEAD in <dir>, the repository this entry is kept for.
    opts, where = c.words[1:len(c.words) - len(args) - 1], here
    for i, x in enumerate(opts):
        if x == "-C" and i + 1 < len(opts):
            where = expand(opts[i + 1], where) or where
        elif x.startswith("-C") and len(x) > 2:  # `-C/path`
            where = expand(x[2:], where) or where
        elif x == "--git-dir" or x.startswith("--git-dir="):
            git_dir = expand(opts[i + 1] if x == "--git-dir" and i + 1 < len(opts) else x.partition("=")[2], where)
            if os.path.basename(git_dir) != ".git":  # a git dir that is not <repo>/.git: any repository
                return {**switched, ANY_REPO: SWITCH_UNKNOWN}
            where = os.path.dirname(git_dir)
        elif x.startswith("--work-tree"):  # the repository it moves is not readable from the words
            return {**switched, ANY_REPO: SWITCH_UNKNOWN}
    key = repo_key(where)
    # -c/-C (switch) and -b/-B (checkout) name the branch to create, attached or as the next word.
    for i, x in enumerate(words):
        if x in ("-c", "-C", "-b", "-B", "--create", "--force-create") and i + 1 < len(words):
            return {**switched, key: words[i + 1]}
        if re.match(r"-[cCbB]", x) and len(x) > 2:
            return {**switched, key: x[2:]}
        if x.startswith(("--create=", "--force-create=")):
            return {**switched, key: x.split("=", 1)[1]}
    names = [x for x in words if not x.startswith("-") or x == "-"]
    # Any other option (--track, --detach, --orphan…) can name a branch this reader does not work out.
    odd = [x for x in words if x.startswith("-") and x != "-" and x not in SWITCH_PLAIN]
    if not names and not odd:
        return switched
    if odd or names[0] == "-" or names[0].startswith("@{"):
        return {**switched, key: SWITCH_UNKNOWN}
    # `git checkout README.md` restores a file; a local branch of that name wins over a path.
    if sub == "checkout" and os.path.exists(os.path.join(where or ".", names[0])) \
            and not git_out(["rev-parse", "--verify", "--quiet", "refs/heads/" + names[0]], where):
        return switched
    return {**switched, key: names[0]}


def repo_key(where):
    """The repository a command in `where` acts on, as a real path: its top-level folder, else `where` itself."""
    return os.path.realpath(git_out(["rev-parse", "--show-toplevel"], where) or where or ".")


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


def check_command(c, masked, cfg, cwd, session, here=None, switched=None):
    """[(key, reason)] for one command. `here` is the directory its relative paths resolve against (cwd after the
    line's earlier `cd`s), `cwd` the hook's own; `switched` maps a repository to the branch an earlier `git switch` on
    the line checked out in it."""
    here = cwd if here is None else here
    out, w, name = [], c.words, c.name
    # Before the argv check: a bare `> file` truncates it with no command at all.
    files = user_files()
    if any(expand(t, here) in files for t in write_targets(c) if t):
        out.append(("guard.deny.user_settings", USER_SETTINGS))
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
        if script and plugin_apply(script, w, here) and not scratch_apply(script, c, cwd, here, masked):
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
    if name in ("git", "gh", "curl") and force_push_default(c, here, switched):
        out.append(("guard.deny.default_force_push", "A force push rewrites the default branch every worker and "
                                                     "reviewer builds on. Push your own PR branch (`git push "
                                                     "--force-with-lease=<branch>:<sha> origin <branch>`); a bad "
                                                     "commit on main is undone with a revert PR."))
    if name in ("gh", "curl") and protection_write(c, here):
        out.append(("guard.deny.protection_write", "This writes branch protection or a ruleset, the rules that "
                                                   "gate merges. The repository owner changes them, not an agent."))
    if name in ("rm", "find"):
        trash, outside = rm_outside(c, here, cwd)
        if trash:
            out.append(("guard.deny.trash_empty", TRASH_EMPTY))
        if outside:
            out.append(("guard.deny.rm_outside", "A recursive `rm` is limited to this checkout and the temporary "
                                                 "folders. Move anything else to the trash (`mv <path> ~/.Trash/`) "
                                                 "or ask the human."))
    if name == "trash-empty" or name == "gio" and w[1:2] == ["trash"] and "--empty" in w \
            or name == "osascript" and finder_empties(w):
        out.append(("guard.deny.trash_empty", TRASH_EMPTY))
    if name == "orch" and w[1:2] == ["exempt"]:
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


def finder_empties(w):
    """Whether an osascript command tells Finder to empty the trash: `to empty`, `empty trash`, or an `empty` line
    (several -e options are lines of one script)."""
    lines = [w[i + 1] if x == "-e" else x[2:] for i, x in enumerate(w) if x == "-e" and i + 1 < len(w)
             or x.startswith("-e") and len(x) > 2]
    script = "\n".join(lines) or " ".join(w)
    # A script line that starts with `empty` is the command, whatever follows (`empty without security`); the last
    # alternative is JXA: `Application("Finder").empty()`.
    return re.search(r"\bFinder\b.*?(?:\bto\s+empty\b|\bempty\s+\(?(?:the\s+)?trash\b\)?|^\s*empty\b|\.empty\s*\()", script,
                     re.I | re.S | re.M) is not None


TRASH_EMPTY = ("This empties a trash folder, where the human keeps what they can still undo. Leave it; move your "
               "own files there instead.")
USER_SETTINGS = ("This writes the user's own settings (Claude Code user settings, ~/.claude/agent-skills.json or "
                 "secure-fill's allowlist). The user changes them: agent-skills:skills-config or tune-automode emits "
                 "a script the user runs.")


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


SCRATCH_VARS = {"HOME", "CLAUDE_CONFIG_DIR", "CLAUDE_SETTINGS", "AGENT_SKILLS_CONFIG", "AGENT_SKILLS_STATE",
                "AGENT_SKILLS_CONFIG_DIR"}
SETTINGS_CHILDREN = ("settings.json", "settings.local.json", "agent-skills.json", "setup-applied.json")
HASH = re.compile(r"[0-9a-f]+")
ASSIGN_WORD = re.compile(r"[A-Za-z_]\w*=")
ABS_WORD = re.compile(r"/[\w./@+-]+")


def scratch_apply(script, c, cwd, here, masked):
    """True only for one narrow shape, an allowlist: the plugin's own `scripts/setup.py --apply HASH --settings-only`,
    run with no wrapper, with every variable that picks where it writes assigned in the command itself to an absolute
    path that resolves (symlinks and `..` followed, also for the files it creates inside) under this checkout's
    worktree and away from the user's real settings. Anything else, an inherited or unset target included, stays
    denied: the other apply scripts take targets from a spec or options this reader does not model."""
    w = c.words
    if not cwd or cwd is UNKNOWN_DIR or here != cwd or not os.path.isdir(cwd):  # a `cd` earlier on the line
        return False
    if expand(script, cwd) != str(ROOT.resolve() / "scripts" / "setup.py") or w[1:2] != [script]:
        return False
    rest = w[2:]
    if "--settings-only" not in rest:  # a full setup also syncs, schedules automations and talks to Orca
        return False
    rest.remove("--settings-only")
    if not (len(rest) == 2 and rest[0] == "--apply" and HASH.fullmatch(rest[1])
            or len(rest) == 1 and re.fullmatch(r"--apply=[0-9a-f]+", rest[0])):
        return False
    # `env -i …`, `command …` and the like, before or after the assignments, can undo them: the words must be six
    # assignments, then the interpreter.
    toks = c.raw.split()
    if len(toks) < 7 or not all(ASSIGN_WORD.match(t) for t in toks[:6]) or toks[6] != w[0]:
        return False
    if re.search(r"(?<![\w./@+-])(?:cd|pushd|popd)(?![\w./@+-])", masked):  # `cd . && …` also leaves `here` equal to `cwd`
        return False
    given = dict(a.split("=", 1) for a in c.assigns)
    if set(given) != SCRATCH_VARS or len(c.assigns) != len(given):
        return False
    top = run(["git", "rev-parse", "--show-toplevel"], cwd).stdout.strip()
    if not top:
        return False
    root = pathlib.Path(os.path.realpath(top))
    real = [pathlib.Path(p) for p in user_files()]

    def inside(value):
        if not ABS_WORD.fullmatch(value):  # quotes, `$`, `~`, globs and relative paths are not read here
            return False
        for p in (pathlib.Path(value), *(pathlib.Path(value, n) for n in SETTINGS_CHILDREN)):
            r = pathlib.Path(os.path.realpath(p))
            if not r.is_relative_to(root) or any(f.is_relative_to(r) or r.is_relative_to(f.parent) for f in real):
                return False
        return True

    def plain(value):  # setup.py backs a file up next to it; a link planted there would be followed out of the tree
        # setup.py backs up beside the resolved file, not beside the link to it: check the value and each settings
        # file inside it, as given and as resolved.
        for p in (value, *(os.path.join(value, n) for n in SETTINGS_CHILDREN)):
            real = os.path.realpath(p)
            for d in {p, os.path.dirname(p), real, os.path.dirname(real)}:
                try:
                    if any(e.is_symlink() and ".bak" in e.name for e in os.scandir(d)):
                        return False
                except OSError:
                    pass
        return True

    return all(inside(v) and plain(v) for v in given.values())


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
    method, endpoint, fields = api_call(w)
    return (method in ("POST", "PUT", "PATCH") and re.search(r"/labels/?$", endpoint) is not None
            and has(f.split("=", 1)[-1] for f in fields))


def api_call(w, here=None):
    """(METHOD, endpoint, [field]) of a `gh api` command. A field or --input without --method means POST."""
    opts = parse_options(w[2:], GH_SHORT, GH_LONG)
    operands = [v for k, v in opts if k is None]
    method = next((v for k, v in reversed(opts) if k in ("-X", "--method")), None)
    fields = [v for k, v in opts if k in ("-f", "-F", "--field", "--raw-field")]
    inputs = [v for k, v in opts if k == "--input"]
    # A body file's text counts as a field: a GraphQL mutation can arrive that way (`--input f`, `-F query=@f`; a
    # raw `-f` field sends `@f` as text).
    typed = [v for k, v in opts if k in ("-F", "--field")]
    fields += [read_body(b, here) for b in inputs + [f.split("=@", 1)[1] for f in typed if "=@" in f]]
    method = (method or ("POST" if fields or inputs else "GET")).upper()
    endpoint = operands[0] if operands else ""
    return method, re.sub(r"^https?://[^/]+", "", endpoint, flags=re.I), fields  # gh takes a full URL too


def parse_options(words, short, long):
    """[(option, value)] of words in order, read as getopt does; an operand is (None, word). A short letter in
    `short` or a long option in `long` takes a value, attached or the next word, even one that looks like an option
    (`-H -XGET`); the letters before it in a cluster are flags (value None). `--` ends the options. curl's `-:` is
    `--next`."""
    opts, i = [], 0
    while i < len(words):
        x = words[i]
        i += 1
        if x == "--":
            opts += [(None, y) for y in words[i:]]
            break
        if x.startswith("--"):
            name, eq, val = x.partition("=")
            if name in long and not eq and i < len(words):
                val, i = words[i], i + 1
            opts.append((name, val if eq or name in long else None))
        elif x.startswith("-") and len(x) > 1:
            for k, ch in enumerate(x[1:], 1):
                if ch in short:
                    val = x[k + 1:]
                    if not val and i < len(words):
                        val, i = words[i], i + 1
                    opts.append(("-" + ch, val))
                    break
                opts.append(("--next" if ch == ":" else "-" + ch, None))
        else:
            opts.append((None, x))
    return opts


def read_body(path, here):
    """The text of a request body file named by `@file`, `--input`, or "" when it is stdin or cannot be read."""
    path = path.removeprefix("@")
    try:
        with open(expand(path, here), encoding="utf-8", errors="replace") as f:  # relative to the command's cwd
            return f.read(65536)
    except OSError:
        return ""


def curl_calls(w, here=None):
    """[(METHOD, [path], [body])] for each transfer of a curl command, split at `--next` as curl does: the path of
    each api.github.com URL it names (`--url` too) and the request bodies (a `@file` read)."""
    out, cur = [], []
    for item in parse_options(w[1:], CURL_SHORT, CURL_LONG) + [("--next", None)]:
        if item[0] != "--next":
            cur.append(item)
            continue
        method = next((v for k, v in reversed(cur) if k in ("-X", "--request")), None)
        data = [v for k, v in cur if k in CURL_DATA]
        get = False  # -G puts -d in the URL; a later --no-get takes it back
        for k, _ in cur:
            if k in ("-G", "--get"):
                get = True
            elif k == "--no-get":
                get = False
        urls = [m.group(1) or "/" for k, v in cur if k in (None, "--url")
                if (m := re.match(r"(?:https?://)?api\.github\.com(?::\d+)?(/[^?#]*)?", v, re.I))]
        bodies = data + [read_body(d, here) for d in data if d.startswith("@") and d != "@-"]
        out.append(((method or ("POST" if data and not get else "GET")).upper(), urls, bodies))
        cur = []
    return out


def protection_write(c, here=None):
    """A `gh api` or curl call that writes branch protection or a ruleset; GETs read them and pass."""
    w = c.words

    def writes(method, endpoint):
        endpoint = re.split(r"[?#]", endpoint, 1)[0]
        # Anchored under a repository or an organization: `contents/.github/rulesets/x.json` is a file, not a ruleset.
        target = re.match(r"/?(?:api/v3/)?(?:repos/[^/]+/[^/]+/branches/.+/protection|(?:repos/[^/]+/[^/]+|orgs/[^/]+)"
                          r"/rulesets)(?:/|$)", endpoint) is not None
        return method not in ("GET", "HEAD", "OPTIONS") and target
    if c.name == "curl":
        return any(any(writes(method, p) for p in paths)
                   or any(p.strip("/") == "graphql" for p in paths) and any(protection_mutation(b) for b in bodies)
                   for method, paths, bodies in curl_calls(w, here))
    if c.name != "gh" or w[1:2] != ["api"]:
        return False
    method, endpoint, fields = api_call(w, here)
    if endpoint.strip("/") == "graphql":
        return any(protection_mutation(f) for f in fields)
    return writes(method, endpoint)


def protection_mutation(text):
    """Whether a request body, a GraphQL document or JSON holding one under "query", creates, updates or deletes a
    branch protection rule or a ruleset."""
    try:
        body = json.loads(text)
        text = body["query"] if isinstance(body, dict) and isinstance(body.get("query"), str) else text
    except ValueError:
        pass
    # A string value or a `#` comment is not a field: a title that names a mutation is not one.
    text = re.sub(r"#[^\n]*", "", re.sub(r'"(?:[^"\\]|\\.)*"', '""', text))
    return (re.search(r"\bmutation\b", text) is not None
            and re.search(r"\b(?:create|update|delete)\w*(?:ProtectionRule|Ruleset)\b", text) is not None)


def git_out(args, where):
    """Stdout of a git command in `where`; "" when it fails or `where` is not a folder."""
    try:
        return run(["git", *args], where).stdout.strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""


def default_names(where, remote, gitdir=()):
    """main, master and the branch the remote's HEAD names in the checkout at `where` (or in `gitdir`)."""
    gitdir = list(gitdir)
    # A remote added by hand, or a URL given in place of one, often has no HEAD of its own and points at origin's
    # address: origin's default branch counts too.
    heads = (git_out(gitdir + ["symbolic-ref", "--short", f"refs/remotes/{r}/HEAD"], where) for r in {remote, "origin"})
    return {"main", "master"} | {h.split("/", 1)[1] for h in heads if "/" in h}


def force_push_default(c, here, switched=None):
    """Whether this `git push`, `gh api` or `curl` command force-updates a default branch: main, master or the
    branch origin/HEAD names. A push with no refspec, or `HEAD`, is judged by the branch checked out in `here`; when
    that cannot be read, only main and master count."""
    w = c.words
    where = here
    for i, x in enumerate(w):  # each -C is relative to the one before it
        if x == "-C" and i + 1 < len(w):
            where = expand(w[i + 1], where) or where
        elif x.startswith("-C") and c.name == "git":  # `-C/path`
            where = expand(x[2:], where) or where
    # `git --git-dir=<dir> push …` acts on that repository, whatever the folder is.
    gitdir = next((["--git-dir", expand(w[i + 1], where) if x == "--git-dir" else expand(x[10:], where)]
                   for i, x in enumerate(w[:-1] + [""]) if x == "--git-dir" or x.startswith("--git-dir=")), [])
    if c.name in ("gh", "curl"):
        if c.name == "gh":
            method, endpoint, fields = api_call(w, where) if w[1:2] == ["api"] else ("", "", [])
            calls = [(method, [endpoint], fields)]
        else:
            calls = curl_calls(w, where)
        names = default_names(where, "origin")
        return any(method == "PATCH" and re.search(r'force"?\s*[=:]\s*true', " ".join(bodies))
                   and any(p.split("?", 1)[0].rsplit("/git/refs/heads/", 1)[-1] in names
                           for p in paths if "/git/refs/heads/" in p) for method, paths, bodies in calls)
    sub, rest = verb(w[1:], GIT_OPTS)
    if sub != "push":
        return False
    opts, pos, i, repo = [], [], 0, None
    while i < len(rest):
        x = rest[i]
        i += 1
        if x == "--":
            pos += rest[i:]
            break
        if x.startswith("--"):  # spell out an abbreviated long option
            name, eq, val = x.partition("=")
            hits = [n for n in PUSH_LONG if n.startswith(name)]
            x = (name if name in PUSH_LONG else hits[0] if len(hits) == 1 else name) + eq + val
        if x.startswith("-") and len(x) > 1:
            opts.append(x)
            if x == "--repo" and i < len(rest):
                repo = rest[i]
            elif x.startswith("--repo="):
                repo = x[7:]
            # A value-taking option reads the next word unless its value is attached: `--repo=x`, `-ofoo`; in a
            # cluster only a closing `o` (`-fo ci.skip`) does.
            i += x in PUSH_VALUED or not x.startswith("--") and x.endswith("o")
        else:
            pos.append(x)

    def short(o):  # the flags of a `-4fn` cluster: a `-o` ends it, the rest is that option's value
        return set(o[1:].split("o", 1)[0]) if not o.startswith("--") else set()
    dry = False  # the last of --dry-run / --no-dry-run wins
    for o in opts:
        if o == "--no-dry-run":
            dry = False
        elif o == "--dry-run" or "n" in short(o):
            dry = True
    if dry:
        return False
    # --force and --force-with-lease are separate switches, each cancelled by its own --no-… form, last one wins.
    # `--no-force-with-lease` starts with `--no-force`, so it is matched before any prefix test.
    force = lease = False
    for o in opts:
        if o == "--force" or "f" in short(o):
            force = True
        elif o == "--no-force":
            force = False
        elif o == "--no-force-with-lease":
            lease = False
        elif o.startswith("--force-with-lease"):
            lease = True
    forced = force or lease
    # --mirror overwrites every remote ref without --force.
    if "--mirror" in opts or forced and ("--all" in opts or "--branches" in opts):
        return True
    named_repo = any(o == "--repo" or o.startswith("--repo=") for o in opts)  # then every positional is a refspec
    remote = (repo or "origin") if named_repo else pos[0] if pos else "origin"
    names = default_names(where, remote, gitdir)
    # --tags alone sends tags only, not the checked-out branch.
    for spec in (pos if named_repo else pos[1:]) or ([] if "--tags" in opts else ["HEAD"]):
        src, _, dst = spec.lstrip("+").partition(":")
        dst = dst if ":" in spec else src
        if spec.lstrip("+") == ":":  # the matching refspec: every branch the remote has
            dst = "*"
        if dst in ("HEAD", "@"):  # `@` is git's alias for HEAD
            # A `git switch` earlier on the line moved HEAD; the repository on disk still shows the old branch.
            seen = switched or {}
            # `--git-dir=<repo>/.git` acts on <repo>; any other git dir is not tied to a folder this reader knows.
            repo = os.path.dirname(gitdir[1]) if gitdir and os.path.basename(gitdir[1]) == ".git" else \
                None if gitdir else repo_key(where)
            moved = seen.get(ANY_REPO) or (seen.get(os.path.realpath(repo)) if repo else None)
            dst = moved if moved is not None else git_out(gitdir + ["symbolic-ref", "--short", "HEAD"], where)
            if dst == SWITCH_UNKNOWN:
                dst = "*"
        # A pattern such as `refs/heads/*` reaches the default branch too.
        if (forced or spec.startswith("+")) and any(fnmatch.fnmatchcase(b, dst) for n in names
                                                    for b in (n, "refs/heads/" + n)):
            return True
    return False


def rm_targets(w):
    """(options, operands) of an `rm` command."""
    opts, operands, end = [], [], False
    for x in w[1:]:
        if not end and x == "--":
            end = True
        elif not end and x.startswith("-") and len(x) > 1:
            opts.append(x)
        else:
            operands.append(x)
    return opts, operands


def trash_dirs():
    home = pathlib.Path.home()
    return [os.path.realpath(home / ".Trash"), os.path.realpath(home / ".local" / "share" / "Trash")]


def in_trash(path):
    return any(path == t or path.startswith(t + os.sep) for t in trash_dirs()) or "/.Trashes/" in path + "/"


def find_roots(w):
    """The start paths of a `find` command that deletes (-delete, or -exec rm); [] without it."""
    if "-delete" not in w and not exec_rms(w):
        return []
    out = []
    # -H -L -P, and the BSD/macOS -d -E -X -x -s, come before the paths; BSD `-f <path>` names one.
    flags = ("-H", "-L", "-P", "-d", "-E", "-X", "-x", "-s", "--")
    take = False
    for x in w[1:]:
        if take:
            out.append(x)
            take = False
        elif x == "-f":
            take = True
        elif x.startswith(("-", "(", "!")) and x not in flags:
            break
        elif x not in flags:
            out.append(x)
    return out or ["."]  # GNU find searches `.` when it is given no path


def rm_outside(c, here, cwd):
    """(trash, outside): the recursive-rm (or `find -delete`) or glob targets that empty a trash folder, and the
    ones that lie outside the checkout the session runs in and the temporary folders. A path with an unresolved `$`
    is not judged."""
    if c.name == "find":
        operands, recursive = find_roots(c.words), True
        # -execdir runs in the found file's folder, so a relative operand is not readable from here.
        execs = [rm_outside(types.SimpleNamespace(name="rm", words=seg), UNKNOWN_DIR if dirwise else here, cwd)
                 for seg, dirwise in exec_rms(c.words)]
    else:
        execs = []
        opts, operands = rm_targets(c.words)
        # GNU rm takes `--recurs` for --recursive: any prefix long enough to be unique.
        recursive = any(len(o) > 2 and "--recursive".startswith(o) and o != "--" or
                        re.fullmatch(r"-[a-zA-Z]*[rR][a-zA-Z]*", o) for o in opts)
    roots = {os.path.realpath(p) for p in ("/tmp", "/private/tmp", os.environ.get("TMPDIR") or "/tmp")}
    top = git_out(["rev-parse", "--show-toplevel"], cwd)
    if top:
        roots.add(os.path.realpath(top))
    trash, outside = [], []
    # `find -L`/`-H` walk through a symlink they start at (the last of -L/-H/-P wins); rm removes the link itself.
    # -H follows only the start paths, -L every link below them.
    walks_links = deep = False
    for y in c.words[1:] if c.name == "find" else []:
        if y in ("-L", "-H", "-P"):
            walks_links, deep = y != "-P", y == "-L"
        elif y not in ("-d", "-E", "-X", "-x", "-s", "--"):  # the other options before the paths
            break
    for x in operands:
        if x == "{}":  # find's placeholder: the paths find walked, already judged
            continue
        path = expand(x, here, follow=walks_links)
        if not path or "$" in path or "`" in path:
            continue
        globbed = re.search(r"[*?\[]", path) is not None
        # bash expands a glob before rm sees it; `~/.Tra[s]h/*` is the trash.
        matches = [os.path.realpath(m) if walks_links else keep_name(m) for m in glob.glob(path)] if globbed else []
        for p in matches or [path]:
            if in_trash(p):
                if recursive or globbed or p in trash_dirs():
                    trash.append(x)
            elif recursive and not any(p.startswith(r + os.sep) or p == r and c.name == "find" for r in roots):
                outside.append(x)
            elif deep and link_escapes(p, roots):
                outside.append(x)
    for t, o in execs:
        trash += t
        outside += o
    return trash, outside


def link_escapes(root, roots, limit=20000):
    """Whether a symlink under root points outside roots: `find -L` walks through it. Looks at the first `limit`
    entries."""
    seen = 0
    for dirpath, dirs, files in os.walk(root):
        for name in dirs + files:
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                target = os.path.realpath(full)
                if not any(target == r or target.startswith(r + os.sep) for r in roots):
                    return True
        seen += len(dirs) + len(files)
        if seen > limit:
            break
    return False


def exec_rms(w):
    """[(words, in_dir)] of each `rm` that `find` runs with -exec, -execdir, -ok or -okdir, up to its `;` or `+`,
    after a wrapper such as `sudo` or `env`. in_dir: it runs in the found file's folder (-execdir, -okdir)."""
    out = []
    for i, x in enumerate(w[:-1]):
        if x in ("-exec", "-execdir", "-ok", "-okdir"):
            seg = []
            for y in w[i + 1:]:
                if y in (";", "+"):
                    break
                seg.append(y)
            while seg and (os.path.basename(seg[0]) in ("sudo", "doas", "env", "command", "nice", "nohup", "time")
                           or seg[0].startswith("-") or "=" in seg[0]):
                seg = seg[1:]
            if seg and os.path.basename(seg[0]) == "rm":
                out.append((seg, x in ("-execdir", "-okdir")))
    return out


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
    cmds, here, pushed, prev = shell.commands(cmd), cwd, [], UNKNOWN_DIR  # prev: where `cd -` goes
    switched = {}  # {repository: the branch an earlier `git switch` on the line checked out}
    outer, start, ticks = [], 0, False  # the directories to go back to at each `)` or closing backtick
    for i, c in enumerate(cmds):
        for ch in masked[start:c.span[0]]:
            if ch == "(" or ch == "`" and not ticks:
                outer.append(here)
                ticks = ticks or ch == "`"
            elif (ch == ")" or ch == "`" and ticks) and outer:
                here = outer.pop()
                ticks = ticks and ch != "`"
        start = c.span[0]
        found = check_command(c, masked, cfg, cwd, session, here, switched)
        # A `cd` in a pipeline or sent to the background (`cd x &`) runs in a subshell; `||` and `&&` do not.
        if c.name in ("cd", "pushd", "popd") and not (
                c.piped_from or c.piped_to or re.match(r"\s*(&(?!&)|\|(?!\|))", masked[c.span[1]:])):
            before = here
            here = after_cd(c, here, prev)
            prev = before
            if c.name == "popd" and pushed:  # the directory the matching `pushd` left
                here = pushed.pop()
            elif c.name == "pushd" and here is not UNKNOWN_DIR:
                pushed.append(before)
        if c.name == "git":
            switched = after_switch(c, switched, here)
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
                                         "and checks the brief file (`--agent codex` for a supervised Codex worker; "
                                         "dispatch-card §3 says which briefs). "
                                         f"{KEY_SOURCE.format(key=key)}"))
    return out


def check_write(path, cfg):
    """[Finding] for an Edit, Write or NotebookEdit of path."""
    key = "guard.deny.user_settings"
    if not isinstance(path, str) or not path or (lvl := level(cfg, key)) == "off":
        return []
    if os.path.realpath(os.path.expanduser(path)) in user_files():
        return [Finding(key, lvl, f"{USER_SETTINGS} {KEY_SOURCE.format(key=key)}")]
    return []
