"""One PR's land judgment, a pure function of facts: no gh, no ledger, no clock.

`orch land-check` and the brief's "next" cell (rowstate.merge_next) used to decide this separately. Each caller still
reads its own facts and words its own text; judge() only classifies them, so both agree on what a PR waits on.

Scope is one PR's own facts. Program-wide and ordering checks (main red, stack order, review threads, visual evidence,
the no-CI base) stay with their callers. The PR-wide rules of a policy:

  check  `orch land-check`: every problem is listed. With CI, a verdict whose patch-id still matches holds across a head
         move; the no-CI lane needs the verdict at the exact head.
  row    the brief's next step: one state, the first that applies. The patch-id is not computed there, so the verdict's
         sha, compared as a prefix either way, stands in for it.

A required fact (REQUIRED, per policy) the caller tried and failed to read is UNREAD: the judgment is `unknown`, naming
it. The patch_match fact may be NOT_COMPUTED, a caller's choice: the policy's stand-in applies. A malformed verdict (no
sha, no pass) reads as a mismatch or a fail instead of raising. Callers pass UNREAD for no other fact.
"""
UNREAD = object()
NOT_COMPUTED = object()

REQUIRED = {"check": ("state", "draft", "merge_state"), "row": ("draft", "merge_state")}


class Judgment:
    """state: check gives "ready" or "hold"; row gives draft, conflict, changes, ci, program_unknown, review, fix or
    ready; either gives "unknown". why: {reason code: detail}, in the order the codes were found."""

    def __init__(self, state, why):
        self.state, self.why = state, why

    def __repr__(self):
        return f"Judgment({self.state!r}, {self.why!r})"


def _prefix(a, b):
    return bool(a) and bool(b) and (a.startswith(b) or b.startswith(a))


def judge(f, policy="check"):
    """Facts f:
      state, draft, merge_state, head                    the PR as GitHub shows it
      changes_requested                                  a reviewer asked for changes
      ci_failed, ci_pending (lists), ci_unknown (bool)   what CI says; ci_unknown: no CI value to read
      stopped, held                                      a STOP line, a parked record
      program                                            False when no single program owns the PR (row only)
      verdict                                            None, or {"pass": bool, "sha": str}
      patch_match                                        True/False, or NOT_COMPUTED
      no_ci                                              the repo has no CI (verdict must be at the exact head)
    """
    for k in REQUIRED[policy]:
        if f.get(k, UNREAD) is UNREAD:
            return Judgment("unknown", {"unknown": k})
    return _check(f) if policy == "check" else _row(f)


def _verdict_reasons(f, why, sha_first):
    v, head = f.get("verdict"), f.get("head") or ""
    if v is None:
        why["no_verdict"] = True
        return
    match = f.get("patch_match", NOT_COMPUTED)
    sha = v.get("sha") or ""
    same_sha = sha == head if sha_first is False else _prefix(head, sha)
    if match is NOT_COMPUTED:
        match = same_sha
    if sha_first:
        if not match:
            why["review"] = True
        elif not v.get("pass"):
            why["fix"] = True
        return
    if not v.get("pass"):
        why["no_verdict"] = True
    elif not match:
        why["patch_changed"] = True
    elif f.get("no_ci") and not same_sha:
        why["no_ci_sha"] = True
    elif not same_sha:
        why["head_moved"] = True  # a note, not a problem: the patch-id still holds


def _check(f):
    why = {}
    if f.get("stopped"):
        why["stopped"] = True
    if f.get("held"):
        why["held"] = True
    if f["state"] != "OPEN" or f["draft"]:
        why["not_open"] = True
    _verdict_reasons(f, why, sha_first=False)
    if f["merge_state"] in ("DIRTY", "BEHIND"):
        why[f["merge_state"].lower()] = True
    if f.get("ci_failed"):
        why["ci_failed"] = list(f["ci_failed"])
    if f.get("ci_pending"):
        why["ci_pending"] = list(f["ci_pending"])
    problems = set(why) - {"head_moved"}
    return Judgment("hold" if problems else "ready", why)


def _row(f):
    """The brief's next step, in the order its cell has always decided: draft, conflict, change request, CI, program,
    review, then ready only with CI success and a clean merge state."""
    why = {}
    if f["draft"]:
        return Judgment("draft", {"draft": True})
    if f["merge_state"] == "DIRTY":
        return Judgment("conflict", {"dirty": True})
    if f.get("changes_requested"):
        return Judgment("changes", {"changes_requested": True})
    if f.get("ci_failed") or f.get("ci_pending"):
        return Judgment("ci", {"ci_failed": list(f.get("ci_failed") or []), "ci_pending": list(f.get("ci_pending") or [])})
    if not f.get("program", True):
        return Judgment("program_unknown", {"program": False})
    _verdict_reasons(f, why, sha_first=True)
    if "review" in why or "no_verdict" in why:
        return Judgment("review", why)
    if "fix" in why:
        return Judgment("fix", why)
    if not f.get("ci_unknown") and f["merge_state"] in ("CLEAN", "HAS_HOOKS"):
        return Judgment("ready", why)
    return Judgment("ci", {"ci_unknown": bool(f.get("ci_unknown")), "merge_state": f["merge_state"]})
