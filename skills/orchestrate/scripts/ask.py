#!/usr/bin/env python3
"""orch ask: the owner's queue of "ask this person to review this PR" (store.py review_ask). watch-prs sends a queued
ask at its next round inside business hours, the first time it finds none for that PR and person in the send-log, and
the row then points at that send-log row. A queue is local state: nothing here leaves the machine.

  add OWNER/REPO#N REVIEWER     queue an ask; the same PR and person again prints the existing id
  list [--status queued|scheduled|sent|reviewed|merged|withdrawn] [--json]
  cancel ID                     withdraw an ask that was not sent (or is not wanted any more)

An ask is queued until a send-log row for the request exists, scheduled while that row is a post still ahead, then sent.
Needs `orch db state` to have moved the send-log into the DB.
"""
import json, sys

import store


def _ready():
    if not store.sendlog_ready():
        sys.exit(f"the review-ask queue is not available: run {store.state_command()} first")


def main(argv):
    sub, rest = (argv or ["help"])[0], argv[1:]
    args = [a for a in rest if not a.startswith("--")]
    try:
        if sub == "add" and len(args) == 2:
            _ready()
            aid, new = store.ask_add(args[0], args[1])
            print(f"{aid} {'queued' if new else 'already queued or asked'}")
        elif sub == "list":
            _ready()
            status = rest[rest.index("--status") + 1] if "--status" in rest else None
            rows = store.asks(status=status)
            if "--json" in rest:
                print(json.dumps(rows, ensure_ascii=False, indent=1))
            else:
                print("\n".join(f"{a['id']}  {a['status']:<9} {a['pr']} → @{a['reviewer']}  ({a['created_at']})" for a in rows)
                      or "no asks")
        elif sub == "cancel" and len(args) == 1 and args[0].isdigit():
            _ready()
            print(f"{args[0]} withdrawn" if store.ask_close(int(args[0])) else f"{args[0]} is not an open ask")
        else:
            sys.exit(__doc__)
    except (store.NotMigrated, store.Refused, ValueError, IndexError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main(sys.argv[1:])
