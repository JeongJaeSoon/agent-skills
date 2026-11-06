"""Which open PRs the land executor may queue on its own (`orchestrate.land_executor.auto_queue`).

Pure: the caller reads GitHub, the ledgers and the request table and passes the facts. A PR is queued only when nothing
is left that the executor would refuse for an input it already has, and when no person or earlier round has decided
about this head. The executor still runs its full checks on the request it takes, so this module only keeps plainly
unready PRs out of the queue; it never replaces those checks.
"""
import re

import landjudge

MODES = ("off", "shadow", "on")
CLEAN = ("CLEAN", "HAS_HOOKS")


def config(raw):
    """{mode, max_new_per_round} from the `auto_queue` value of the settings, off for anything missing or malformed."""
    off = {"mode": "off", "max_new_per_round": 1}
    if not isinstance(raw, dict) or raw.get("mode") not in MODES:
        return off
    n = raw.get("max_new_per_round", 1)
    if type(n) is not int or n < 1:
        return off
    return {"mode": raw["mode"], "max_new_per_round": n}


def names_head(body, head):
    """Does the comment name the head: a whole hex token of 7 to 40 characters that abbreviates it."""
    return any(head.lower().startswith(t.lower()) for t in re.findall(r"(?<![0-9A-Za-z])[0-9A-Za-z]{7,40}(?![0-9A-Za-z])", body or ""))


def withdrawn(events, pr):
    """A land that ended at exit 1 withdraws its call (prog.stop_land). Only a verdict or a `ready` written after it
    brings the PR back: a new head needs its own verdict anyway, and `ready` is what `orch land` writes."""
    state = False
    for e in events:
        if e.get("pr") != pr:
            continue
        if e.get("ev") == "land_check" and e.get("outcome") == "stopped":
            state = True
        elif e.get("ev") in ("ready", "verdict"):
            state = False
    return state


def decide(c, late):
    """(queue?, reasons). Reasons are why not, in the order checked; an empty list means queue.

    c: owners [slugs of every program whose ledger names the PR], owner_autonomous, programs_unread (a ledger that could
    not be read might own the PR), requested (a request row of any status for this program, PR and head), withdrawn,
    main_red, verdict_problem (prog.executor_verdict_problem, None when it stands), facts (landjudge facts, CI not yet).
    late() reads what costs a call and is asked only for a PR that got this far: {ci_failed, ci_pending, sticky}."""
    why = []
    if c.get("programs_unread"):
        return False, ["a program ledger could not be read, so the owner is unknown"]
    owners = c.get("owners") or []
    if len(owners) != 1:
        return False, ["no program owns it" if not owners else f"{len(owners)} programs name it: {', '.join(sorted(owners))}"]
    if not c.get("owner_autonomous"):
        return False, [f"{owners[0]} is not an autonomous program"]
    if c.get("requested"):
        return False, ["a land request for this head exists already"]
    if c.get("withdrawn"):
        return False, ["its land was stopped: a new verdict or `orch land` brings it back"]
    if c.get("main_red"):
        why.append("main is red")
    if c.get("verdict_problem"):
        why.append(c["verdict_problem"])
    f = c["facts"]
    j = landjudge.judge(f, "check")
    if j.state == "unknown":
        why.append(f"unread: {j.why['unknown']}")
    else:
        why += [k for k in j.why if k not in ("head_moved", "ci_failed", "ci_pending")]
    if f.get("merge_state") not in CLEAN and "dirty" not in j.why and "behind" not in j.why:
        why.append(f"merge state {f.get('merge_state')}")
    if why:
        return False, why
    more = late()
    j = landjudge.judge({**f, "ci_failed": more["ci_failed"], "ci_pending": more["ci_pending"]}, "check")
    why += [f"{k}: {', '.join(v)}" for k, v in j.why.items() if k in ("ci_failed", "ci_pending")]
    if not more["sticky"]:
        why.append("no test-results comment names this head")
    return (not why), why
