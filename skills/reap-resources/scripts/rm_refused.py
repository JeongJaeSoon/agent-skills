#!/usr/bin/env python3
"""The record of cards whose `orca worktree rm` a permission layer refused, and the one predicate that reads it.

Usage:
  python3 rm_refused.py add PATH      record that the removal of the card at PATH was refused (appends to the file)
  python3 rm_refused.py list          the recorded cards still on disk

A refused act is not run again, so a recorded card leaves every automatic cleanup: cards.py scan and apply, the
cardguard check both cleaners pass, reap-hard, and the CLOSE OUT line of `orch wait`. The person runs the one command
`command()` prints. The file is <state>/rm-refused.jsonl, one JSON object per line: {"path", "ino", "t"}.
An entry names a card by path and folder inode: a card made again on that path is a new card, and a card
whose folder is gone needs nothing. Stdlib only: orchestrate's prog.py imports it too.
"""
import json, os, pathlib, shlex, sys, time

NAME = "rm-refused.jsonl"


def state_dir():
    return pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser()


def real(path):
    return os.path.realpath(os.path.expanduser(str(path)))


def ino_of(path):
    try:
        return os.stat(path).st_ino
    except (OSError, ValueError):
        return None


def read(state=None):
    """{real path: entry} for the recorded cards that still exist as the same folder; None when the file exists and
    cannot be read: a caller keeps the card then, since a refusal it cannot see is no proof it may remove."""
    f = pathlib.Path(state or state_dir()) / NAME
    try:
        lines = f.read_text().splitlines()
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    out = {}
    for line in lines:
        if not line.strip():
            continue
        try:
            e = json.loads(line)
            p = real(e["path"])
        except (ValueError, KeyError, TypeError):
            return None
        if e.get("ino") is not None and ino_of(p) == e["ino"]:
            out[p] = e
    return out


def held(path, state=None):
    """The entry for the card at path, False when there is none, None when the record cannot be read."""
    got = read(state)
    return None if got is None else got.get(real(path), False)


def command(path):
    """The one line a person runs: the refused act itself."""
    return f"orca worktree rm --worktree path:{shlex.quote(real(path))} --run-hooks"


def add(path, state=None, now=None):
    p = real(path)
    if not os.path.isdir(p):
        raise SystemExit(f"rm_refused: {p} is not a folder")
    d = pathlib.Path(state or state_dir())
    d.mkdir(parents=True, exist_ok=True)
    row = {"path": p, "ino": ino_of(p), "t": now or time.time()}
    with open(d / NAME, "a") as f:
        f.write(json.dumps(row) + "\n")
    return row


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["add"] and len(args) == 2:
        add(args[1])
        print(f"recorded: {real(args[1])}")
    elif args == ["list"]:
        got = read()
        if got is None:
            sys.exit("rm_refused: the record cannot be read")
        for p in sorted(got):
            print(f"{p}\t{command(p)}")
    else:
        sys.exit(__doc__)
