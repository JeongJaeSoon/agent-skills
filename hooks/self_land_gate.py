#!/usr/bin/env python3
"""PreToolUse hook of the agent-skills plugin: a program card given the land does not close over its own open PR.

A card lands its own PR (orchestrate references/landing.md). Stopping at READY is for a wait only a person or another
PR can end, and `worker_done` names it with a `READY-WAIT: <kind>` line. This hook looks at the two calls that settle a
card, a `worker_done` that is not `--outcome failed` and `orca worktree rm` on this card, and speaks only when all hold:

- the caller is a worker's main conversation, whose first prompt has a `PROGRAM: <slug>` line and an `APPROVED` line
  naming `orch land`. A card without them was never given the merge, so a READY stop is what its brief asked for.
- the program's merge_policy is not human-gate
- no READY-WAIT line (a `worktree rm` has none to give: `end-session` §4 keeps a card whose PR is open)
- `gh` shows an open PR from this branch in the program's repo

The level is `orchestrate.self_land` in ~/.claude/agent-skills.json: warn by default, deny to enforce, off to read
nothing. What cannot be read (the first prompt, program.json, the branch, gh, a body held in a variable) is never
judged: the call passes with one warning line. `orca terminal close` is not seen, since closing only the terminal over
an open PR is a legitimate ending (`end-session` §7, §8), and neither is a Stop, since a turn's end is no card's end.
A command inside a script or `bash -c` is not seen: a guardrail, not a sandbox.
"""
import json, os, pathlib, re, subprocess, sys

import lifecycle, roles
from lib import config, shell

KEY = "orchestrate.self_land"
MARK = "[gate:self-land]"
SOURCE = f"(agent-skills hooks/self_land_gate.py, {KEY})"
WAITS = ("owner-merge", "human-review", "land-refused", "land-after", "external-wait")
# Read from the raw text: a body may be a quoted heredoc, which argv does not show.
WAIT_LINE = re.compile(rf"(?:^|[\s\"'])READY-WAIT:[ \t]*(?:{'|'.join(WAITS)})(?![\w-])", re.M)
PROGRAM_LINE = re.compile(r"^[ \t]*PROGRAM:?[ \t]+([\w.-]+)[ \t]*$", re.M)
APPROVED_LAND = re.compile(r"^[ \t]*APPROVED\b.*\borch land\b", re.M)
GH_TIMEOUT = 6


class Unread(Exception):
    """A fact this hook needs could not be read; the call passes with this one line."""


def option(words, name):
    """The value of `--name value` or `--name=value`, or None."""
    for i, w in enumerate(words):
        if w == name and i + 1 < len(words):
            return words[i + 1]
        if w.startswith(name + "="):
            return w[len(name) + 1:]
    return None


def git(cwd, *args):
    r = subprocess.run(["git", "-C", cwd or ".", *args], capture_output=True, text=True, timeout=3)
    return r.stdout.strip() if r.returncode == 0 else ""


def settling(cmd, cwd):
    """What in this command settles the card: ("remove", None), ("report", its body or ""), or None. A removal wins, so a
    report's READY-WAIT line cannot excuse an rm in the same command."""
    if lifecycle.removes_own(cmd, cwd):
        return "remove", None
    for c in shell.commands(cmd):
        if c.name == "orca" and c.words[1:3] == ["orchestration", "send"] and option(c.words, "--type") == "worker_done" \
                and option(c.words, "--outcome") != "failed":
            return "report", option(c.words, "--body") or ""
    return None


def program(slug):
    home = pathlib.Path(os.environ.get("PROGRAMS_HOME") or "~/.claude/programs").expanduser()
    try:
        cfg = json.loads((home / slug / "program.json").read_text())
        return cfg["repo"], cfg.get("merge_policy")
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise Unread(f"program {slug} is unreadable ({type(e).__name__})")


def open_prs(repo, cwd):
    try:
        branch = git(cwd, "branch", "--show-current")
        if not branch:
            raise Unread("the card's branch is unknown")
        r = subprocess.run(["gh", "pr", "list", "--repo", repo, "--head", branch, "--state", "open", "--json", "number,url"],
                           capture_output=True, text=True, timeout=GH_TIMEOUT)
        if r.returncode != 0:
            raise Unread("gh pr list failed")
        return json.loads(r.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        raise Unread(f"gh pr list gave no answer ({type(e).__name__})")


def check(event, cmd):
    """The open PRs that should have landed first; [] when this call is fine."""
    cwd = event.get("cwd")
    act = settling(cmd, cwd)
    if not act or event.get("agent_id"):
        return act, []
    first = roles.first_prompt(event.get("transcript_path"))
    if not first:
        raise Unread("the first prompt is unreadable")
    who = roles.session(event, first)
    slug = PROGRAM_LINE.search(first)
    if not roles.applies("worker_only", who) or not slug or not APPROVED_LAND.search(first):
        return act, []
    repo, policy = program(slug[1])
    if policy == "human-gate":
        return act, []
    kind, body = act
    if kind == "report" and WAIT_LINE.search(cmd):
        return act, []
    prs = open_prs(repo, cwd)
    if prs and kind == "report" and "$" in body:
        raise Unread("the worker_done body is a shell value")
    return act, prs


def reason(kind, prs):
    names = ", ".join(f"#{p.get('number')}" for p in prs)
    if kind == "remove":
        return (f"{MARK} {SOURCE} {names} is still open on this card: `end-session` §4 keeps the worktree. "
                "Land it with `orch land`, or send worker_done with its READY-WAIT line and keep the card.")
    return (f"{MARK} {SOURCE} {names} is still open and this card was given the land. Land it with `orch land`, or, for "
            f"a wait only someone else can end, add one line to the --body: READY-WAIT: {' | '.join(WAITS)}. "
            "A card that did not finish sends --outcome failed. Enforce with orchestrate.self_land: deny.")


def emit(decision, text):
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse"}}
    if decision:
        out["hookSpecificOutput"].update(permissionDecision=decision, permissionDecisionReason=text)
    else:
        out["systemMessage"] = text
        out["hookSpecificOutput"]["additionalContext"] = text
    return out


def run(event):
    cmd = (event.get("tool_input") or {}).get("command") if isinstance(event, dict) else None
    if not isinstance(cmd, str) or "orca" not in cmd:
        return None
    level = config.level(config.load(), KEY, "warn")
    if level == "off":
        return None
    try:
        act, prs = check(event, cmd)
        if prs:
            return emit(None if level == "warn" else level, reason(act[0], prs))
    except Unread as e:
        return emit(None, f"agent-skills hooks/self_land_gate.py passed this call unjudged: {e}.")
    except Exception as e:
        config.log_error("self_land_gate", e)
        return emit(None, f"agent-skills hooks/self_land_gate.py could not finish ({type(e).__name__}) and let this call through.")
    return None


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if out := run(event):
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
