#!/usr/bin/env python3
"""orch order: the owner's instructions as rows (store.py order_, order_link), so the brief can say which ones nothing
carries yet. A session that accepts an instruction as work writes it here before it delegates or starts.

  setup                                add the tables to agent-skills.db (a recorded step); a second run changes nothing
  add TEXT [--kind order|feedback] [--source S] [--owner-action]   prints the new id (oN)
  link oN --unit card:<worktree>|agent:<id>|board:<bN>|decision:<dN>|pr:<owner/repo#number> [--stand-in]
                                       --stand-in: the unit only stands for the order (the temporary to-do an import
                                       replaced). It is shown, but does not carry the order: the brief still counts it
                                       as unlinked, and a closed stand-in is never evidence the order was done
  done oN | drop oN                    close it by hand; nothing closes one by itself yet
  list [--status open|done|dropped] [--json]
  import FILE [--skip REGEX] [--dry-run | --check]   numbered lines of a hand list (`## section` then `1. text`) become
                                       kind=feedback rows, once each: the row keeps the line's key as its source, so a
                                       second run skips what it already took. The file is only read.
                                       --check lists the lines not taken yet and exits 1 while there are any
"""
import hashlib, json, os, pathlib, re, sys

import fleet, store

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
            r = store.order_close(oid, "done" if sub == "done" else "dropped", _by())
            print(f"{r['id']} {r['status']}")
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
