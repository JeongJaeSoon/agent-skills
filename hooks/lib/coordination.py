"""Coordination rules for role sessions (orchestrate references/top-level.md, "Asking the human" and the wait rules):
which session they reach is roles.applies' matrix, how hard they hold is a key in ~/.claude/agent-skills.json.

- orchestrate.ask_user_question (deny): AskUserQuestion from the main conversation of an orchestrator, a lead or a
  worker. It blocks the turn until someone answers, and nobody watching the coordinator's or worker's terminal does.
- orchestrate.wait_foreground (deny): `orch wait`, `orch inbox --wait`, `orch watch-cards` or `orca orchestration
  check|inbox --wait` from an orchestrator or a lead outside run_in_background, or a background one cut by `| tail`
  or `| head` (its output is read only at the end).
- orchestrate.send_devnull (deny): `orca orchestration send|reply` with its output sent to /dev/null, which hides
  whether the message went out.
- orchestrate.model_flag (warn): with `orchestrate.models` set, an `orca orchestration worker-start`, `orca worktree
  create` or `orca agent launch` of Claude from any session with no `--model`. `--terminal` reuses a running session,
  which takes no model. A `worker-start` whose `--model` differs from `orchestrate.models.worker` also warns, and
  one warning says the value was not compared when that key is unset. These never go above warn.
- orchestrate.pr_poll (warn): a Monitor command that polls `gh pr list|view|checks` in a loop with a sleep under 300 s.
  The warning points at scripts/pr_watch.py. It never goes above warn.
"""
import re

import roles
from lib import config, shell
from lib.danger import Finding

RULES = {"orchestrate.ask_user_question": "deny", "orchestrate.wait_foreground": "deny",
         "orchestrate.send_devnull": "deny", "orchestrate.model_flag": "warn", "orchestrate.pr_poll": "warn"}
LAUNCHES = (["orchestration", "worker-start"], ["worktree", "create"], ["agent", "launch"])
SOURCE = "(agent-skills hooks/guard.py, {key})"


def finding(cfg, key, reason):
    lvl = config.level(cfg, key, RULES[key])
    return [] if lvl == "off" else [Finding(key, lvl, f"{reason} {SOURCE.format(key=key)}")]


def check_ask(s, cfg):
    if not roles.applies("ask_user_question", s):
        return []
    return finding(cfg, "orchestrate.ask_user_question",
                   f"This session's role is {s.role}, and AskUserQuestion blocks its turn until someone answers this "
                   "terminal. Put the question in your reply (orchestrator, lead) or send it with `orca orchestration "
                   "ask` (a dispatched worker) or `--type question` to its Run (a card), then end the turn.")


def waits(c):
    w = c.words
    if c.name == "orch" and len(w) > 1:
        return w[1] in ("wait", "watch-cards") or w[1] == "inbox" and "--wait" in w
    return c.name == "orca" and w[1:3] in (["orchestration", "check"], ["orchestration", "inbox"]) and "--wait" in w


def downstream(cmds, c):
    """The commands c's own pipeline feeds, in order."""
    out, i = [], next(i for i, d in enumerate(cmds) if d is c)
    while cmds[i].piped_to and i + 1 < len(cmds) and cmds[i + 1].piped_from:
        i += 1
        out.append(cmds[i])
    return out


def flag(words, name):
    """The value of `name` in argv, written `--x v` or `--x=v`; None when absent."""
    for i, w in enumerate(words):
        if w == name:
            return words[i + 1] if i + 1 < len(words) else ""
        if w.startswith(name + "="):
            return w[len(name) + 1:]
    return None


def check_model_flag(cmds, cfg):
    if not config.get(cfg, "orchestrate.models"):
        return []
    out = []
    want = config.get(cfg, "orchestrate.models.worker")
    for c in cmds:
        w = c.words
        if not (c.name == "orca" and w[1:3] in LAUNCHES and option(w, "--agent") == "claude"
                and option(w, "--terminal") is None):
            continue
        have = option(w, "--model")
        if not have:
            out += finding(cfg, "orchestrate.model_flag",
                           f"This `orca {' '.join(w[1:3])}` names no model, so the session runs on the account's default. "
                           "Add `--model` with what `orch model worker` (or `lead`) prints.")
        elif w[1:3] != LAUNCHES[0] or expands(c, have):
            continue  # only a worker start is a worker; a value the shell expands cannot be read here
        elif not isinstance(want, str) or not want:
            if not any("is not set" in f.reason for f in out):
                out += warn_only(cfg, "A `--model` is not compared: orchestrate.models.worker is not set.")
        elif not same_model(have, want):
            out += warn_only(cfg, f"`--model {have}` differs from orchestrate.models.worker ({want}). "
                                  "Use what `orch model worker` prints, or change the setting.")
    return out


TEXT_FLAGS = ("--spec", "--prompt", "--display-name", "--name", "--comment")


def option(words, name):
    """The value of `name` in a launch's words, or None. The value of a text flag (--spec, ...) is skipped whatever
    it says; any other `--flag value` pair consumes its value unless that looks like another flag. Orca takes the
    last of a repeated option, so this does too."""
    i, found = 0, None
    while i < len(words):
        w, nxt = words[i], words[i + 1] if i + 1 < len(words) else None
        if w == name:
            found = "" if nxt is None or nxt.startswith("--") else nxt
        elif w.startswith(name + "="):
            found = w[len(name) + 1:]
        i += 2 if w in TEXT_FLAGS or (w.startswith("--") and "=" not in w and nxt and not nxt.startswith("--")) else 1
    return found


def expands(c, value):
    """True when `value` holds a character bash may expand ($ ` * ? [ { ~). The words arrive unquoted, so a quoted one
    cannot be told from a bare one without re-parsing the shell; the guard only warns, so it skips what it is unsure of."""
    return re.search(r"[$`*?\[{~-]", value) is not None  # the last range: shell.py's stand-ins for `$` and `~`


def warn_only(cfg, reason):
    """A comparison finding never goes above warn, even when orchestrate.model_flag is set to deny or ask."""
    return [f._replace(level="warn") for f in finding(cfg, "orchestrate.model_flag", reason)]


def same_model(have, want):
    """Equal ignoring case, a `[1m]` suffix and a `-YYYYMMDD` date. A bare family (`sonnet`) matches any id of it."""
    have, want = (re.sub(r"-\d{8}$", "", m.split("[")[0].lower()) for m in (have, want))
    families = ("opus", "sonnet", "haiku")
    for fam, other in ((have, want), (want, have)):
        if fam in families:
            # `claude-sonnet-4-6` and `claude-3-7-sonnet-…` are the family; `opusplan` and the like are not
            return other == fam or re.fullmatch(rf"claude-(\d+(-\d+)*-)?{fam}(-.*)?", other) is not None
    return have == want


PR_POLLS = (["pr", "list"], ["pr", "view"], ["pr", "checks"])
PR_POLL_MIN = 300
SLEEP_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


def sleep_seconds(c):
    """Seconds a `sleep` command waits; None when it is not one or its argument is not a plain number."""
    if c.name != "sleep":
        return None
    total = 0.0
    for x in c.words[1:]:
        m = re.fullmatch(r"(\d+\.?\d*|\.\d+)([smhd]?)", x)
        if not m:
            return None
        total += float(m[1]) * SLEEP_UNITS[m[2]]
    return total


def pr_verb(words):
    """['pr', 'view'] for `gh [-R repo] pr view …`: gh's global repo option may come before the subcommand."""
    rest, i = words[1:], 0
    while i < len(rest) and rest[i].startswith("-"):
        i += 2 if rest[i] in ("-R", "--repo") else 1
    return rest[i:i + 2]


def at_command_start(text, idx):
    """True when the word at idx can begin a command: after a separator, or after `do|then|else|elif` that itself
    begins one. In `echo do for` the `for` is an argument."""
    before = text[:idx].rstrip(" \t")
    if not before or before[-1] in ";&|(){\n":
        return True
    m = re.search(r"(?<![\w.-])(do|then|else|elif)$", before)
    return bool(m) and at_command_start(text, m.start())


def loop_around(text, cmds, pos):
    """(start, end) of the innermost `until|while|for … done` that holds offset pos in text (quoted words already
    blanked). The loop's condition counts: `until gh pr view …; do sleep 30; done` polls in it. The parser hands
    back each closing `done` as a command of its own, so those are exact; an opening keyword is told from an
    argument by where it stands."""
    marks = [(m.start(), "open") for m in re.finditer(r"(?<![\w.-])(until|while|for)(?![\w.-])", text)
             if at_command_start(text, m.start())]
    # text has quoted words blanked, so a quoted "done" (an ordinary command) does not close the loop.
    marks += [(c.span[1], "close") for c in cmds
              if c.name == "done" and c.words == ["done"] and text[c.span[0]:c.span[1]].split()[:1] == ["done"]]
    starts, best = [], None
    for at, kind in sorted(marks):
        if kind == "open":
            starts.append(at)
        elif starts:
            start = starts.pop()
            if start <= pos < at and (best is None or start > best[0]):
                best = (start, at)
    return best


def check_monitor(cmd, cfg):
    """A Monitor loop that polls `gh pr list|view|checks` with a sleep under 300 s wakes the session on every
    state change, most of them with nothing to do. The shared watcher prints only the changes that need a turn."""
    cmds, text = shell.commands(cmd), shell.command_text(cmd)
    # The wait that counts is the one inside the loop around a poll (a sleep before the loop is a one-off), and the
    # sleeps there add up (`sleep 150; sleep 150` waits 300 s). A sleep put in the background (`sleep 300 &`) waits
    # for nobody, so it counts as 0. Where the loop branches (if/else/case/`||`) only one sleep may run, so the
    # shortest one stands for the loop (a heuristic: it can warn on a loop that branches yet always waits long
    # enough, which costs a warning, never a block). A `wait` in the loop makes a background sleep a real wait. A
    # sleep whose argument is a variable cannot be judged, so that loop passes without a warning. A poll outside any
    # loop runs once and is no watcher. Any one short loop warns.
    for poll in (c for c in cmds if c.name == "gh" and pr_verb(c.words) in PR_POLLS):
        region = loop_around(text, cmds, poll.span[1] - 1)  # a span starts at the blank before the word
        if region is None:
            continue
        scope = [c for c in cmds if region[0] < c.span[1] <= region[1]]
        sleeps = [c for c in scope if c.name == "sleep"]
        secs = [sleep_seconds(c) for c in sleeps]
        if not secs or None in secs:
            continue
        if not any(c.words == ["wait"] for c in scope):  # `wait $!` waits for one job, not for the sleep
            secs = [0 if re.match(r"\s*&(?!&)", text[c.span[1]:]) else s for c, s in zip(sleeps, secs)]
        span = text[region[0]:region[1]]
        branches = re.search(r"(?<![\w.-])(if|case|else|elif)(?![\w.-])|\|\|", span)
        wait = min(secs) if branches else sum(secs)
        if wait < PR_POLL_MIN:
            break
    else:
        return []
    return finding(cfg, "orchestrate.pr_poll",
                   f"This Monitor polls `gh pr` every {wait:g} s, and each state change wakes the session. Use "
                   "skills/orchestrate/scripts/pr_watch.py: it checks every 300 s or more and prints only a merge or "
                   "close, a CI failure, READY, a conflict appearing or clearing, and a new PR.")


def check_bash(cmd, background, s, cfg):
    out, cmds = [], shell.commands(cmd)
    out += check_model_flag(cmds, cfg)
    if roles.applies("wait_foreground", s):
        wait = next((c for c in cmds if waits(c)), None)
        if wait and not background:
            out += finding(cfg, "orchestrate.wait_foreground",
                           f"`{' '.join(wait.words[:3])}` blocks this session's turn ({s.role}) until mail arrives. Run the same "
                           "command with run_in_background: true and end your turn; its completion notice wakes you.")
        elif wait and any(c.name in ("tail", "head") for w in cmds if waits(w) for c in downstream(cmds, w)):
            out += finding(cfg, "orchestrate.wait_foreground",
                           "A background wait piped into `tail` or `head` shows nothing until it ends. Run it without "
                           "the pipe and read its output file.")
    if roles.applies("send_devnull", s):
        for c in cmds:
            if c.name == "orca" and c.words[1:3] in (["orchestration", "send"], ["orchestration", "reply"]) and any(
                    t == "/dev/null" and (op in ("&>", "&>>") or op in (">", ">>", ">|", ">&") and fd in ("", "1"))
                    for fd, op, t in c.redirects):
                out += finding(cfg, "orchestrate.send_devnull",
                               "Its output says whether the message was delivered (`\"ok\": true`). Keep it: drop "
                               "the /dev/null redirection.")
                break
    return out
