#!/usr/bin/env python3
"""Pick the independent reviewer for a change: Codex, or a fresh-context Claude reviewer.

  review_pick.py [--repo DIR] [--timeout SECONDS]

Prints one line, `<codex|claude|none> <why>`, and exits
  0  picked: run that reviewer and record it as `review:codex` or `review:claude`
  2  ~/.claude/agent-skills.json is not valid JSON, or review.reviewer has an unknown value
  3  review.reviewer is codex and Codex cannot be used: the review waits (prints `none`)

review.reviewer (in ~/.claude/agent-skills.json, $AGENT_SKILLS_CONFIG moves it):
  auto   (default) Codex when `codex login status` answers within the timeout, else Claude
  codex  Codex only; exit 3 when it does not answer
  off    never call Codex; always the Claude reviewer (the review itself is never skipped)
A repository with `repos.<owner/repo>.external_llm: false` always gets Claude, whatever the setting:
its code does not go to an outside model, and hooks/lib/danger.py refuses the codex call there anyway.
"""
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

DEFAULTS = {"reviewer": "auto"}
VALUES = ("auto", "codex", "off")


def load_config():
    path = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG") or "~/.claude/agent-skills.json").expanduser()
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        print(f"none {path} cannot be read as JSON ({e}); fix it first")
        sys.exit(2)
    return data if isinstance(data, dict) else {}


def repo_of(path):
    """owner/repo from the checkout's origin URL; None when unknown."""
    try:
        url = subprocess.run(["git", "remote", "get-url", "origin"], cwd=path, capture_output=True,
                             text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def codex_ready(timeout):
    """None when `codex login status` exits 0 within timeout, else why not."""
    try:
        r = subprocess.run(["codex", "login", "status"], capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return "codex is not on PATH"
    except subprocess.TimeoutExpired:
        return f"`codex login status` gave no answer in {timeout:g} s"
    except OSError as e:
        return f"codex could not start ({e.strerror})"
    if r.returncode:
        detail = (r.stderr or r.stdout).strip().splitlines()
        return f"`codex login status` exited {r.returncode}" + (f": {detail[0]}" if detail else "")
    return None


def pick(cfg, repo, timeout):
    """(reviewer, why, exit code)."""
    section = cfg.get("review") if isinstance(cfg.get("review"), dict) else {}
    setting = section.get("reviewer", DEFAULTS["reviewer"])
    if setting not in VALUES:
        return "none", f"review.reviewer is {setting!r}, not one of {' | '.join(VALUES)}", 2
    repos = cfg.get("repos") if isinstance(cfg.get("repos"), dict) else {}
    if repo and isinstance(repos.get(repo), dict) and repos[repo].get("external_llm") is False:
        return "claude", f"repos.{repo}.external_llm is false: its code does not go to an outside model", 0
    if setting == "off":
        return "claude", "review.reviewer is off: Codex is not called", 0
    why = codex_ready(timeout)
    if why is None:
        return "codex", f"review.reviewer is {setting} and Codex answered", 0
    if setting == "codex":
        return "none", f"review.reviewer is codex and {why}: the review waits for Codex", 3
    return "claude", f"{why}: falling back to the fresh-context Claude reviewer", 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=".", help="the checkout under review (default: the working directory)")
    ap.add_argument("--timeout", type=float, default=10.0, help="seconds to wait for `codex login status`")
    args = ap.parse_args(argv)
    reviewer, why, code = pick(load_config(), repo_of(args.repo), args.timeout)
    print(f"{reviewer} {why}")
    return code


if __name__ == "__main__":
    sys.exit(main())
