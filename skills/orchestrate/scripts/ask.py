#!/usr/bin/env python3
"""orch ask: the owner's queue of "ask this person to review this PR" (store.py review_ask). watch-prs sends a queued
ask at its next round inside business hours, the first time it finds none for that PR and person in the send-log, and
the row then points at that send-log row. A queue is local state: nothing here leaves the machine.

  add OWNER/REPO#N REVIEWER     queue an ask; the same PR and person again prints the existing id
  list [--status queued|scheduled|sent|reviewed|merged|withdrawn] [--json]
  cancel ID                     withdraw an ask that was not sent (or is not wanted any more)
  import FILE [--apply]         one-time read of a legacy review-outbox log: a plain `queued` line becomes an ask. Without
                                --apply it only prints what would happen. Every other line is left alone and counted with
                                its reason (sent, scheduled, on hold, withdrawn, not asked, superseded, check, unparsed).

An ask is queued until a send-log row for the request exists, scheduled while that row is a post still ahead, then sent.
Needs `orch db state` to have moved the send-log into the DB.
"""
import json, re, sys

import store


def _ready():
    if not store.sendlog_ready():
        sys.exit(f"the review-ask queue is not available: run {store.state_command()} first")


_LINE = re.compile(r"^\S+ (?P<pr>[\w.-]+/[\w.-]+#\d+) head \w+ reviewer=(?P<who>[^\s(]+)\([^)]*\) (?P<rest>.*)$")
_DONE = re.compile(r"\b(SENT|SCHEDULED|BATCH|ON HOLD|WITHDRAWN|NOT asked|SUPERSEDED)\b")


def outbox_line(line):
    """(pr, reviewer, None) for a plain queued line, else (None, None, reason). The log is current state edited in place, so
    the status is the first word after `reviewer=` and an earlier state kept in `(was: ...)` is cut away first. Anything
    that ever left the queue (sent, scheduled, held, withdrawn) is not queued again: asking twice is worse than missing one."""
    line = line.strip()
    if not line:
        return None, None, "blank"
    m = _LINE.match(line)
    if not m:
        return None, None, ("check" if " CHECK " in line else "superseded" if "SUPERSEDED" in line else "unparsed")
    rest = m["rest"].split("(was:")[0]
    if rest.startswith("queued") and not _DONE.search(rest):
        return m["pr"], m["who"], None
    hit = _DONE.search(rest)
    return None, None, hit[1].lower() if hit else "unparsed"


def import_outbox(path, apply):
    """Read the log at `path`; with apply, queue each plain queued line. Returns (queued, skipped counts)."""
    queued, skipped = [], {}
    for line in open(path, encoding="utf-8").read().splitlines():
        pr, who, why = outbox_line(line)
        if why:
            skipped[why] = skipped.get(why, 0) + 1
        else:
            queued.append((pr, who))
    out = []
    for pr, who in queued:
        if apply:
            aid, new = store.ask_add(pr, who, by="orch ask import")
            out.append((pr, who, aid, new))
        else:
            out.append((pr, who, None, None))
    return out, skipped


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
        elif sub == "import" and len(args) == 1:
            _ready()
            rows, skipped = import_outbox(args[0], "--apply" in rest)
            for pr, who, aid, new in rows:
                print(f"{pr} → @{who}  " + ("would queue" if aid is None else f"{aid} {'queued' if new else 'already queued or asked'}"))
            print(f"{len(rows)} queued line(s); left alone: " + (", ".join(f"{k} {v}" for k, v in sorted(skipped.items())) or "none")
                  + ("" if "--apply" in rest else "  [dry run: add --apply]"))
        else:
            sys.exit(__doc__)
    except (store.NotMigrated, store.Refused, ValueError, IndexError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main(sys.argv[1:])
