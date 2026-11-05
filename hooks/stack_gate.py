#!/usr/bin/env python3
"""PreToolUse hook of the agent-skills plugin: a PR that depends on another open PR is a GitHub stack.

On Bash `gh pr create`, the PRs this one depends on are:
- the open PRs its brief names (the `stacked on` value of the session's first prompt's ORDER line), by `#N` or by a
  ticket key in the PR's title or head branch. A brief is fixed at dispatch: a name that no open PR answers to may
  have landed since, so it warns instead of enforcing. ORDER's `lands after` is a deploy order (base on the default
  branch, an `orch dep` edge), not a stack, and is not read.
- the open PRs whose head is in this branch and not yet on its base.
A dependency that is not among the PRs below the base (the base's PR, that PR's base's PR, …) is a gap: the call gets
a warning, or a deny under `orchestrate.stack_dependent: deny` in ~/.claude/agent-skills.json (`off` reads nothing).
`orch land` checks the same key on `orch dep` edges and on the real stack: at creation the PR does not exist yet, so
a base is all there is to see. Same paths are not a dependency and stay with `orch overlap`.

What cannot be read (`--repo`, a head on a fork, a value in a variable, gh failing, an unreadable transcript) passes
with one warning line. A wrapper (`bash -c`, a script) is not seen: a guardrail, not a sandbox.
"""
import json, os, pathlib, re, subprocess, sys

import roles
from lib import config, shell

KEY = "orchestrate.stack_dependent"
OPEN_LIMIT = 200
ORDER = re.compile(r"(?im)^[ \t]*`?ORDER\b.*?\bstacked[ \t]+on\b:?(.*)$")
NEXT_PART = re.compile(r"·|\b(?:starts|lands)[ \t]+after\b", re.I)
# A brief written before `stacked on` existed named a stack with `lands after`.
LEGACY = re.compile(r"(?im)^[ \t]*`?ORDER\b.*?\blands[ \t]+after\b:?(.*)$")
TICKET = re.compile(r"(?<![\w-])[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+(?![\w-])")
PR_REF = re.compile(r"(?<![\w/])#(\d+)\b")
SENTENCE_END = re.compile(r"[.。](?:\s|$)")


class Unread(Exception):
    """What the verdict needs could not be read; the message says what."""


def run(args, cwd):
    """The command's stripped stdout, None when it failed."""
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=6)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def create_call(command, cwd):
    """(arguments after `gh pr create`, the directory it runs in), or None."""
    here = cwd or "."
    for c in shell.commands(command):
        w = c.words
        if w[:1] == ["cd"] and len(w) == 2:
            here = os.path.join(here, os.path.expanduser(w[1]))
        elif w[:3] == ["gh", "pr", "create"] or (w[1:3] == ["pr", "create"] and pathlib.Path(w[0]).name == "gh"):
            return w[3:], here
    return None


def option(args, long, short):
    for i, a in enumerate(args):
        if a in (long, short):
            return args[i + 1] if i + 1 < len(args) else ""
        if a.startswith(long + "="):
            return a[len(long) + 1:]
        if a.startswith(short) and not a.startswith("--") and len(a) > 2:
            return a[2:]
    return None


def named_deps(prompt, pattern=ORDER):
    """(ticket keys, PR numbers) of the first prompt's ORDER `stacked on` (or another ORDER part's pattern)."""
    m = pattern.search(prompt)
    if not m:
        return [], []
    value = NEXT_PART.split(SENTENCE_END.split(m.group(1))[0])[0]
    return TICKET.findall(value), [int(n) for n in PR_REF.findall(value)]


def resolve(keys, numbers, rows):
    """({open PR number: why}, the names no open PR answers to)."""
    found, missing = {}, []
    for key in keys:
        hits = [r["number"] for r in rows if re.search(rf"(?<![A-Z0-9]){re.escape(key)}(?!\d)",
                                                        f"{r.get('headRefName')} {r.get('title')}".upper())]
        found.update({n: f"brief: stacked on {key}" for n in hits})
        missing += [] if hits else [key]
    for n in numbers:
        if any(r["number"] == n for r in rows):
            found[n] = f"brief: stacked on #{n}"
        else:
            missing.append(f"#{n}")
    return found, missing


def below(base, rows):
    """The open PRs under `base`: its own PR, that PR's base's PR, and so on."""
    heads = {r["headRefName"]: r for r in rows}
    chain = []
    while base in heads and heads[base]["number"] not in chain:
        chain.append(heads[base]["number"])
        base = heads[base]["baseRefName"]
    return chain


def verdict(args, here, prompt):
    """(gaps as {PR: why}, the PRs already below the base, warnings). Raises Unread."""
    if option(args, "--repo", "-R") is not None:
        raise Unread("`--repo` names a repository this checkout may not hold")
    head = option(args, "--head", "-H") or run(["git", "branch", "--show-current"], here)
    if not head or ":" in head or "$" in head:
        raise Unread("the head branch is not a local branch name")
    base = option(args, "--base", "-B")
    if base is None:
        base = run(["git", "config", f"branch.{head}.gh-merge-base"], here) or ""
    if "$" in base:
        raise Unread("the base is in a variable")
    # Not narrowed to the author: a dependency on someone else's open PR is one all the same.
    listed = run(["gh", "pr", "list", "--state", "open", "--limit", str(OPEN_LIMIT),
                  "--json", "number,title,headRefName,baseRefName,headRefOid"], here)
    try:
        rows = [r for r in json.loads(listed) if r["headRefName"] != head]
    except (TypeError, ValueError, KeyError):
        raise Unread("`gh pr list` did not answer")
    upstream = f"origin/{base}" if base else "origin/HEAD"
    mine = run(["git", "rev-list", head, "--not", upstream, "--"], here)
    if mine is None:
        raise Unread(f"`git rev-list {head} --not {upstream}` failed")
    keys, numbers = named_deps(prompt)
    deps, missing = resolve(keys, numbers, rows)
    commits = set(mine.split())
    for r in rows:
        if r["headRefOid"] in commits:
            deps.setdefault(r["number"], f"this branch contains its head {r['headRefName']}")
    # A default branch that is itself the head of an open PR (main into a release branch) is a trunk, not a lower layer.
    trunk = upstream == run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], here)
    chain = [] if trunk else below(base, rows)
    warnings = []
    if len(rows) >= OPEN_LIMIT - 1:
        warnings.append(f"stack_gate: only the first {OPEN_LIMIT} open PRs were read.")
    if missing:
        warnings.append(f"stack_gate: the brief says this is stacked on {', '.join(missing)}, and no open PR answers"
                        " to that. If it has landed, nothing to do; if not, stack on it.")
    if not ORDER.search(prompt):
        old = sorted(n for n in resolve(*named_deps(prompt, LEGACY), rows)[0] if n not in chain and n not in deps)
        if old:
            warnings.append(f"stack_gate: the brief's ORDER says lands after {', '.join(f'#{n}' for n in old)} and"
                            " has no `stacked on`, so it may predate the split: a deploy order keeps this base, but"
                            " if this PR uses that PR's code it is a stack (`--base <its branch>`).")
    return {n: why for n, why in deps.items() if n not in chain}, chain, warnings


def transcript_unread(event):
    path = event.get("transcript_path")
    return bool(path) and not os.access(path, os.R_OK)


def message(gaps, base):
    named = "; ".join(f"#{n} ({why})" for n, why in sorted(gaps.items()))
    return (f"This PR depends on open {named}, but its base `{base or 'the default branch'}` does not build on it. "
            "A PR that uses another's code is a GitHub stack: open it with `--base <the lower PR's branch>` (a linear "
            "stack of at most 3 layers, one layer per base, rebased, never merged into), then link them with "
            "`gh stack link <lower> <this> --base <trunk>` so they land in one merge. If it only edits the same files, "
            "the work waits until that PR lands; if it only has to deploy after it, keep the default branch and record the "
            f"order with `orch dep`. ({KEY} in ~/.claude/agent-skills.json: warn, deny or off)")


def emit(deny, warnings):
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse"}}
    if deny:
        out["hookSpecificOutput"].update(permissionDecision="deny", permissionDecisionReason=f"[gate:stack] {deny}")
    if warnings:
        out["systemMessage"] = "\n".join(warnings)
        out["hookSpecificOutput"]["additionalContext"] = "\n".join(warnings)
    if deny or warnings:
        print(json.dumps(out, ensure_ascii=False))


def main():
    event = json.load(sys.stdin)
    command = (event.get("tool_input") or {}).get("command")
    if event.get("tool_name") != "Bash" or not isinstance(command, str):
        return
    call = create_call(command, event.get("cwd"))
    level = config.level(config.load(), KEY, "warn") if call else "off"
    if level == "off":
        return
    args, here = call
    try:
        if transcript_unread(event):
            raise Unread("the session's first prompt")
        gaps, stacked, warnings = verdict(args, here, roles.first_prompt(event.get("transcript_path")))
    except Unread as e:
        return emit(None, [f"stack_gate: could not read {e}; `gh pr create` passes unchecked."])
    if stacked and not gaps:
        warnings.append(f"stack_gate: based on #{stacked[0]}. Once this PR exists, link them: `gh stack link`.")
    base = option(args, "--base", "-B") or ""
    if gaps and level == "deny":
        return emit(message(gaps, base), warnings)
    emit(None, warnings + ([message(gaps, base)] if gaps else []))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        config.log_error("stack_gate", e)
    sys.exit(0)
