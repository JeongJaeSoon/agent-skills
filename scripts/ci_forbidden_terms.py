#!/usr/bin/env python3
"""Scan the tree, this run's commit messages and the PR's title and body for the private list of forbidden terms.

Usage: FORBIDDEN_TERMS=<one ERE per line> [PR_NUMBER=n BASE_SHA=s HEAD_SHA=s] python3 scripts/ci_forbidden_terms.py

The list stays out of the repository (AGENTS.md), and this repository's Actions logs are public. So the output is
fixed text and counts only: no term, no path or line, no sha, which would point at the public sentence that holds a
term. Find the place with the local grep (write-skill §4). Child stderr is dropped because a bad pattern's error
quotes the pattern. The title and body come from the API, since a re-run reuses the old event payload.
"""
import os, pathlib, re, subprocess, sys, tempfile

WARN_SKIPPED = "::warning::FORBIDDEN_TERMS is not set (a fork PR, or no secret yet); forbidden-term scan skipped"
HIT, MISS = 0, 1
ZERO_SHA = re.compile(r"^0+$")


class Failed(Exception):
    """The scan did not finish; the message is fixed text."""


def child(cmd, text=None):
    env = {k: v for k, v in os.environ.items() if k != "FORBIDDEN_TERMS"}
    return subprocess.run(cmd, input=text, env=env, capture_output=True, text=True, errors="replace")


def count_text(text, terms):
    p = child(["grep", "-c", "-i", "-E", "-f", terms], text)
    if p.returncode not in (HIT, MISS):
        raise Failed("a pattern in FORBIDDEN_TERMS is invalid; check the list locally")
    return int(p.stdout.strip() or 0)


def count_tree(terms):
    p = child(["git", "grep", "-l", "-I", "-i", "-E", "-f", terms])
    if p.returncode not in (HIT, MISS):
        raise Failed("git grep failed; check the patterns in FORBIDDEN_TERMS locally")
    return len(p.stdout.splitlines())


def commit_messages():
    base, head = os.environ.get("BASE_SHA", ""), os.environ.get("HEAD_SHA", "") or "HEAD"
    span = ["-1", head] if not base or ZERO_SHA.match(base) else [f"{base}..{head}"]
    p = child(["git", "log", "--format=%B", *span])
    if p.returncode:
        raise Failed("could not read the commit range")
    return p.stdout


def pr_text(number):
    p = child(["gh", "api", f"repos/{os.environ.get('GITHUB_REPOSITORY', '')}/pulls/{number}",
               "--jq", '.title + "\\n" + (.body // "")'])
    if p.returncode:
        raise Failed("could not read the PR title and body")
    return p.stdout


def main():
    patterns = [line.strip() for line in os.environ.get("FORBIDDEN_TERMS", "").splitlines() if line.strip()]
    if not patterns:
        print(WARN_SKIPPED)
        return 0
    with tempfile.TemporaryDirectory() as d:  # mode 700, and the patterns never reach an argv
        terms = pathlib.Path(d) / "terms.tmp"
        terms.write_text("\n".join(patterns) + "\n")
        hits = {"tree": count_tree(terms), "commit messages": count_text(commit_messages(), terms)}
        number = os.environ.get("PR_NUMBER")
        if number:
            hits["PR title and body"] = count_text(pr_text(number), terms)
    for where, n in hits.items():
        print(f"{where}: {n}")
    if any(hits.values()):
        print("::error::forbidden terms found; find them with the local grep (write-skill step 4)")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Failed as e:
        print(f"::error::{e}")
        sys.exit(1)
