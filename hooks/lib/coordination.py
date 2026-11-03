"""Coordination rules for role sessions (orchestrate references/top-level.md, "Asking the human" and the wait rules):
which session they reach is roles.applies' matrix, how hard they hold is a key in ~/.claude/agent-skills.json.

- orchestrate.ask_user_question (deny): AskUserQuestion from the main conversation of an orchestrator, a lead or a
  worker. It blocks the turn until someone answers, and nobody watching the coordinator's or worker's terminal does.
- orchestrate.wait_foreground (deny): `orch wait`, `orch inbox --wait`, `orch watch-cards` or `orca orchestration
  check|inbox --wait` from an orchestrator or a lead outside run_in_background, or a background one cut by `| tail`
  or `| head` (its output is read only at the end).
- orchestrate.send_devnull (deny): `orca orchestration send|reply` with its output sent to /dev/null, which hides
  whether the message went out.
"""
import roles
from lib import config, shell
from lib.danger import Finding

RULES = {"orchestrate.ask_user_question": "deny", "orchestrate.wait_foreground": "deny",
         "orchestrate.send_devnull": "deny"}
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


def check_bash(cmd, background, s, cfg):
    out, cmds = [], shell.commands(cmd)
    if roles.applies("wait_foreground", s):
        wait = next((c for c in cmds if waits(c)), None)
        if wait and not background:
            out += finding(cfg, "orchestrate.wait_foreground",
                           f"`{' '.join(wait.words[:3])}` blocks this session's turn ({s.role}) until mail arrives. Run the same "
                           "command with run_in_background: true and end your turn; its completion notice wakes you.")
        elif wait and wait.piped_to and any(c.name in ("tail", "head") for c in downstream(cmds, wait)):
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
