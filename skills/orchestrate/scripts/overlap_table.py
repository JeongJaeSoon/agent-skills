"""The hot-file table of `orch overlap --table`: which files two or more open PRs change, and the order to land them in.

Pure formatting and ordering; the caller reads the PRs. The order is advice: it does not change `orch land`'s order
(class, aging, dependencies, stacks; references/landing.md), and two PRs sharing a file are not a dependency.
"""

LANDABLE = ("CLEAN", "HAS_HOOKS")


def is_ready(f):
    """Nothing on the PR is left to fix: a verdict at the current head, CI ok, a clean merge state, not a draft."""
    return f["verdict"] == "current" and f["ci"] == "ok" and f["merge_state"] in LANDABLE and not f["draft"]


def describe(f):
    base = f"verdict {f['verdict']}, CI {f['ci']}, {f['merge_state']}"
    return base + (", draft" if f["draft"] else "") + ("" if f["base"] == "main" else f", base {f['base']}")


def hot_table(files, rows, max_open=None, unread=(), unknown=()):
    """Lines for the files changed by two or more PRs.

    files {pr: [paths]}; rows {pr: {verdict: current|stale|fail|none, ci: ok|pending|failed, merge_state, draft, base}};
    max_open: an OVER line for each file with more open PRs than this; unread: PRs whose file list could not be read;
    unknown: other inputs that could not be read, named in the UNKNOWN lines."""
    by_file = {}
    for n, paths in files.items():
        for p in paths:
            by_file.setdefault(p, []).append(n)
    hot = sorted(((p, ns) for p, ns in by_file.items() if len(ns) > 1), key=lambda kv: (-len(kv[1]), kv[0]))
    out = []
    for p, ns in hot:
        facts = {n: rows.get(n) for n in ns}
        order = sorted(ns, key=lambda n: (not (facts[n] and is_ready(facts[n])), n))
        out.append(f"HOT {p}: {len(ns)} open PRs")
        for i, n in enumerate(order, 1):
            f = facts[n]
            if f is None:
                out.append(f"  {i} #{n} wait: facts unknown")
                continue
            ready = is_ready(f)
            mark = "  <- land first" if ready and i == 1 else ""
            out.append(f"  {i} #{n} {'ready' if ready else 'wait'}: {describe(f)}{mark}")
        if not any(facts[n] and is_ready(facts[n]) for n in ns):
            out.append("  no PR is ready")
    if not out:
        out = [f"no hot file among {len(files)} open PR(s)"]
    out += [f"OVER {p}: {len(ns)} open PRs, max_open {max_open}" for p, ns in hot if max_open and len(ns) > max_open]
    out += [f"UNKNOWN #{n}: its file list could not be read" for n in unread]
    out += [f"UNKNOWN {what}: could not be read" for what in unknown]
    return out
