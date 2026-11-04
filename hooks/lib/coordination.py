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
"""
import re

import roles
from lib import config, shell
from lib.danger import Finding

RULES = {"orchestrate.ask_user_question": "deny", "orchestrate.wait_foreground": "deny",
         "orchestrate.send_devnull": "deny", "orchestrate.model_flag": "warn"}
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
        if not (c.name == "orca" and w[1:3] in LAUNCHES and flag(w, "--agent") == "claude" and flag(w, "--terminal") is None):
            continue
        have = flag(w, "--model")
        if not have:
            out += finding(cfg, "orchestrate.model_flag",
                           f"This `orca {' '.join(w[1:3])}` names no model, so the session runs on the account's default. "
                           "Add `--model` with what `orch model worker` (or `lead`) prints.")
        elif w[1:3] != LAUNCHES[0] or re.search(r"[$`*?\[]", re.sub(r"\[1m\]$", "", have)):
            continue  # only a worker start is a worker; a value the shell expands (variable, glob) cannot be read here
        elif not isinstance(want, str) or not want:
            if not any("is not set" in f.reason for f in out):
                out += warn_only(cfg, "A `--model` is not compared: orchestrate.models.worker is not set.")
        elif not same_model(have, want):
            out += warn_only(cfg, f"`--model {have}` differs from orchestrate.models.worker ({want}). "
                                  "Use what `orch model worker` prints, or change the setting.")
    return out


def warn_only(cfg, reason):
    """A comparison finding never goes above warn, even when orchestrate.model_flag is set to deny or ask."""
    return [f._replace(level="warn") for f in finding(cfg, "orchestrate.model_flag", reason)]


def same_model(have, want):
    """Equal ignoring case, a `[1m]` suffix and a `-YYYYMMDD` date. A bare family (`sonnet`) matches any id of it."""
    have, want = (re.sub(r"-\d{8}$", "", m.split("[")[0].lower()) for m in (have, want))
    families = ("opus", "sonnet", "haiku")
    for fam, other in ((have, want), (want, have)):
        if fam in families:
            return other == fam or other.startswith(f"claude-{fam}-")  # not `opusplan` and the like
    return have == want


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
