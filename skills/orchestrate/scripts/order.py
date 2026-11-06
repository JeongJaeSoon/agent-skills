#!/usr/bin/env python3
"""orch order: the owner's instructions as rows (store.py order_, order_link), so the brief can say which ones nothing
carries yet. A session that accepts an instruction as work writes it here before it delegates or starts.

  setup                                add the tables to agent-skills.db (a recorded step); a second run changes nothing
  add TEXT [--kind order|feedback] [--source S] [--owner-action]   prints the new id (oN)
  link oN --unit card:<worktree>|agent:<id>|board:<bN>|decision:<dN>|pr:<owner/repo#number> [--stand-in]
                                       --stand-in: the unit only stands for the order (the temporary to-do an import
                                       replaced). It is shown, but does not carry the order: the brief still counts it
                                       as unlinked, and a closed stand-in is never evidence the order was done
  done oN | drop oN [--reason TEXT]    close it by hand. For kind=feedback, done needs a real pr: link whose PR gh shows
                                       merged (the merge commit is kept in the event; a stand-in, to-do or card is not
                                       evidence) and drop needs --reason. tidy closes one itself only with
                                       orchestrate.order_auto_close true (off by default); until then it records a
                                       would_close event for each order it would have closed (see shadow)
  reopen oN                            a closed order is open again, and its links-complete mark is cleared; tidy does
                                       not close it again on the evidence it was closed on
  links-complete oN [--undo]           say every unit the order needs is linked (needs one real link). Only an order with
                                       this mark is a candidate for the automatic close. A real link added later clears
                                       it: say it again
  shadow                               the orders tidy would have closed, set against what the sessions did (agree,
                                       pending, differs, reopened) and the ones closed by hand that were never a
                                       candidate (missed, with what holds them now). Exits 1 on differs or reopened
  show oN [--json]                     the order's text and links; the card start reads it with --json
  list [--status open|done|dropped] [--json]
  import FILE [--skip REGEX] [--dry-run | --check]   numbered lines of a hand list (`## section` then `1. text`) become
                                       kind=feedback rows, once each: the row keeps the line's key as its source, so a
                                       second run skips what it already took. The file is only read.
                                       --check lists the lines not taken yet and exits 1 while there are any
"""
import hashlib, json, os, pathlib, re, subprocess, sys

import fleet, order_close, store

USAGE = __doc__.split("\n\n", 1)[0] + "\n" + __doc__.split("\n\n", 1)[1]


def opt(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv[:-1] else default


def _by():
    return os.environ.get("ORCA_TERMINAL_HANDLE")


def parse_list(text, name):
    """(source, text) for each numbered line of a hand list; the `## ` heading above it is part of the key and the text.
    `name` tells two lists apart, so it carries more than the file's basename."""
    section, out = "", []
    for line in text.splitlines():
        if m := re.match(r"#{1,3}\s+(.+?)\s*$", line):
            section = m.group(1)
        elif m := re.match(r"(\d+)\.\s+(.+?)\s*$", line):
            # The key holds hashes, not words: masking a heading or a file name must not eat the number after it.
            tag = hashlib.sha1(section.encode()).hexdigest()[:12]
            out.append((f"import:{name}:{tag}#{m.group(1)}", f"{section}: {m.group(2)}" if section else m.group(2)))
    return out


def cmd_import(rest):
    path = rest[0] if rest and not rest[0].startswith("--") else None
    if not path:
        sys.exit("usage: orch order import FILE [--skip REGEX] [--dry-run | --check]")
    try:
        skip = re.compile(opt(rest, "--skip")) if opt(rest, "--skip") else None
    except re.error as e:
        sys.exit(f"--skip is not a regular expression: {e}")
    p = pathlib.Path(path).resolve()
    name = hashlib.sha1(str(p).encode()).hexdigest()[:12]  # no file name: masking would eat what follows it
    items = [(s, t) for s, t in parse_list(p.read_text(), name) if not (skip and skip.search(t))]
    if not items:
        sys.exit(f"{path}: no numbered lines to import")
    if "--dry-run" in rest or "--check" in rest:
        taken = store.order_sources([s for s, _ in items])
        # A line taken under the same key but with other words is changed: the table holds the old one, and a real
        # import would refuse it, so it is not migrated either.
        left = [(s, t) for s, t in items if taken.get(fleet.mask(s)) != fleet.mask(t.strip())]
        for s, t in left:
            word = "changed   " if fleet.mask(s) in taken else "unmigrated" if "--check" in rest else "would add "
            print(f"{word}  {fleet.mask(s)}  {fleet.mask(t)[:100]}")
        print(f"{len(left)} not migrated of {len(items)}")
        if left and "--check" in rest:
            sys.exit(1)  # the original still holds orders the table does not
        return
    added = 0
    for s, t in items:
        row, new = store.order_add(t, "feedback", s, by=_by(), dedupe=True)
        added += new
        print(f"{row['id']}  {'added' if new else 'kept '}  {row['source']}")
    print(f"{added} added, {len(items) - added} already there")


def cmd_list(rest):
    rows = store.orders(opt(rest, "--status"))
    if rows is None:
        raise store.NotMigrated(f"orders are not set up: run {store.order_setup_command()}")
    if "--json" in rest:
        return print(json.dumps(rows, ensure_ascii=False, indent=1))
    for o in rows:
        flags = f"{o['status']}{' owner' if o['owner_action'] else ''}"
        o["links"] = [u + (" (stand-in)" if u in o["stand_ins"] else "") for u in o["links"]]
        print(f"{o['id']}  {o['created_at']}  {o['kind']}  {flags}  "
              f"{' '.join(o['links']) or 'no link'}  {o['text'].splitlines()[0][:100]}")


def merged(pr):
    """The merge commit of `owner/repo#N` when GitHub says it is merged, else None (also when gh cannot say)."""
    repo, _, num = pr.partition("#")
    try:
        p = subprocess.run([os.environ.get("ORCH_GH", "gh"), "pr", "view", num, "--repo", repo, "--json", "state,mergeCommit"],
                           capture_output=True, text=True, timeout=30)
        d = json.loads(p.stdout) if p.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    sha = (d.get("mergeCommit") or {}).get("oid")
    return sha if d.get("state") == "MERGED" and sha else None


def close(oid, sub, reason):
    """done or drop. A feedback row is not closed on the session's word: `done` needs a PR linked to it (a real link,
    not a stand-in) that GitHub shows merged, and `drop` needs --reason. The merge commit goes in the close event."""
    row = store.order_get(oid)
    # The units as they are now go in the close event, so a Reopen can keep them (tidy does not close the order again
    # on the same evidence). None when a unit is not ended or cannot be read: Reopen then reads them again.
    snap = order_close.snapshot_now(oid) if sub != "drop" and row["status"] == "open" else None
    if row["kind"] != "feedback":
        return store.order_close(oid, "dropped" if sub == "drop" else "done", _by(), reason=reason, snapshot=snap)
    # The checks below run inside the close transaction, and only on a row that is open there. A row read as closed
    # carries none, so a reopen between the read and the write is refused instead of closed without evidence.
    if sub == "drop":
        def need_reason(_):
            if not (reason or "").strip():
                raise ValueError(f"{oid} is feedback: drop it with --reason TEXT (why it needs no change)")
        return store.order_close(oid, "dropped", _by(), reason=reason, check=need_reason)
    evidence = {}
    if row["status"] == "open":
        prs = [u[3:] for u in row["links"] if u.startswith("pr:") and u not in row["stand_ins"]]
        if not prs:
            raise ValueError(f"{oid} is feedback: link the PR that fixed it first "
                             f"(orch order link {oid} --unit pr:<owner/repo#number>); a to-do or card is not evidence")
        evidence = {pr: sha for pr in prs if (sha := merged(pr))}
        if not evidence:
            raise ValueError(f"{oid} is feedback: none of {', '.join(prs)} is merged (per gh); done waits for the merge")

    def still(now_row):  # the links as written, not as read before the network call
        if not any(f"pr:{pr}" in now_row["links"] and f"pr:{pr}" not in now_row["stand_ins"] for pr in evidence):
            raise ValueError(f"{oid}: the merged PR link changed, or the order was reopened, while closing; run it again")
    return store.order_close(oid, "done", _by(), evidence={"merged_prs": evidence} if evidence else None, reason=reason,
                             check=still, snapshot=snap)


def main(argv):
    sub, rest = (argv or ["help"])[0], argv[1:]
    oid = rest[0] if rest and not rest[0].startswith("--") else None
    try:
        if sub == "setup":
            print(store.order_setup())
        elif sub == "add" and oid:
            row, _ = store.order_add(oid, opt(rest, "--kind", "order"), opt(rest, "--source"), "--owner-action" in rest, _by())
            print(row["id"])
        elif sub == "link" and oid and opt(rest, "--unit"):
            stand_in = "--stand-in" in rest
            print(f"{oid} {'linked' if store.order_link(oid, opt(rest, '--unit'), _by(), stand_in) else 'already linked'} "
                  f"{opt(rest, '--unit')}{' (stand-in)' if stand_in else ''}")
        elif sub in ("done", "drop") and oid:
            r = close(oid, sub, opt(rest, "--reason"))
            print(f"{r['id']} {r['status']}")
        elif sub == "reopen" and oid:
            # A close by hand keeps no snapshot of its units, so read them now: tidy then waits for them to change.
            r = store.order_reopen(oid, _by(), snapshot=order_close.snapshot_now(oid))
            print(f"{r['id']} {r['status']}")
        elif sub == "shadow":
            lines, wrong = order_close.compare()
            print("\n".join(lines))
            sys.exit(1 if wrong else 0)
        elif sub == "links-complete" and oid:
            r = store.order_links_complete(oid, _by(), undo="--undo" in rest)
            print(f"{r['id']} links {'complete at ' + r['links_complete_at'] if r['links_complete_at'] else 'not marked complete'}")
        elif sub == "show" and oid:
            r = store.order_get(oid)
            if "--json" in rest:
                return print(json.dumps(r, ensure_ascii=False, indent=1))
            print(f"{r['id']}  {r['kind']}  {r['status']}  {' '.join(r['links']) or 'no link'}\n{r['text']}")
        elif sub == "list":
            cmd_list(rest)
        elif sub == "import":
            cmd_import(rest)
        else:
            sys.exit(USAGE)
    except store.NotMigrated as e:
        sys.exit(f"orders are not available ({e})")
    except (ValueError, OSError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main(sys.argv[1:])
